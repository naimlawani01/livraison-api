"""
Endpoints pour le partage de localisation client.

Flux :
1. Le partenaire crée une commande → un location_token est généré
2. Le partenaire envoie le lien (contenant le token) au client via WhatsApp/SMS
3. Le client ouvre le lien → page HTML qui demande sa position GPS
4. Le client autorise → sa position est envoyée ici et sauvegardée
"""
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field

from ....core.database import get_db
from ....models.commande import Commande
from ....models.user import User
from ....utils.dependencies import get_current_user

router = APIRouter()


class LocationSubmit(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class GenerateLocationLinkResponse(BaseModel):
    location_token: str
    link: str


@router.post("/commandes/{commande_id}/location-link", response_model=GenerateLocationLinkResponse)
async def generate_location_link(
    commande_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Générer un lien de localisation pour une commande"""
    query = select(Commande).where(Commande.id == commande_id)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        raise HTTPException(status_code=404, detail="Commande non trouvée")

    if commande.location_token:
        token = commande.location_token
    else:
        token = secrets.token_urlsafe(32)
        commande.location_token = token
        await db.commit()

    return GenerateLocationLinkResponse(
        location_token=token,
        link=f"/loc/{token}"
    )


@router.get("/loc/{token}", response_class=HTMLResponse)
async def location_page(token: str, db: AsyncSession = Depends(get_db)):
    """Page HTML publique : partage de position → prix → paiement / tracking."""
    query = select(Commande).where(Commande.location_token == token)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        return HTMLResponse(content=_error_html("Lien invalide ou expiré"), status_code=404)

    # Position déjà partagée → on redirige vers la suite naturelle
    # • MM avec checkout_url disponible et paiement non confirmé → page paiement
    # • Sinon → page de tracking
    if commande.location_shared_at:
        from fastapi.responses import RedirectResponse
        from ....models.commande import ModePaiement

        if (
            commande.mode_paiement == ModePaiement.MOBILE_MONEY
            and commande.geniuspay_checkout_url
            and commande.paiement_confirme != "oui"
        ):
            return RedirectResponse(url=commande.geniuspay_checkout_url, status_code=302)
        if commande.tracking_token:
            return RedirectResponse(url=f"/suivi/{commande.tracking_token}", status_code=302)
        return HTMLResponse(content=_already_shared_html())

    return HTMLResponse(content=_location_html(token))


@router.post("/loc/{token}/submit")
async def submit_location(
    token: str,
    data: LocationSubmit,
    db: AsyncSession = Depends(get_db),
):
    """Recevoir la position GPS du client (endpoint public, pas d'auth).

    Cet endpoint déclenche aussi :
      1. Le calcul du prix final (distance × M_colis × M_heure)
      2. La création du lien de paiement GeniusPay (si Mobile Money)
      3. La diffusion de la commande aux livreurs (si Cash, ou MM en fallback)

    Retourne les URLs nécessaires à la page web pour orienter le client :
    bouton "Payer" (MM) ou "Suivre" (Cash).
    """
    import logging
    from ....core.config import settings
    from ....models.commande import CommandeStatus, ModePaiement
    from ....models.partenaire import Partenaire
    from ....services.geolocation_service import GeolocationService
    from ....services.matching_service import MatchingService

    logger = logging.getLogger(__name__)

    query = select(Commande).where(Commande.location_token == token)
    result = await db.execute(query)
    commande: Optional[Commande] = result.scalar_one_or_none()

    if not commande:
        raise HTTPException(status_code=404, detail="Lien invalide")

    # Si déjà partagée, on retourne juste les URLs pour rediriger
    tracking_url = f"{settings.PUBLIC_BASE_URL}/suivi/{commande.tracking_token}"

    if commande.location_shared_at:
        return {
            "message": "Position déjà enregistrée",
            "already_shared": True,
            "mode_paiement": commande.mode_paiement.value if hasattr(commande.mode_paiement, "value") else commande.mode_paiement,
            "prix": commande.prix_propose,
            "checkout_url": commande.geniuspay_checkout_url,
            "tracking_url": tracking_url,
        }

    # ── 1. Sauvegarder la position ────────────────────────────────────────
    commande.latitude_client = data.latitude
    commande.longitude_client = data.longitude
    commande.location_shared_at = datetime.now(timezone.utc)

    # ── 2. Recalculer le prix selon distance + nature_colis ──────────────
    partenaire_q = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
    partenaire_r = await db.execute(partenaire_q)
    partenaire: Optional[Partenaire] = partenaire_r.scalar_one_or_none()

    if partenaire and partenaire.latitude and partenaire.longitude:
        distance_km = GeolocationService.calculer_distance(
            (partenaire.latitude, partenaire.longitude),
            (data.latitude, data.longitude),
        )
        # Import ici pour éviter import circulaire avec commandes.py
        from .commandes import calculer_prix
        prix_final = calculer_prix(distance_km, commande.nature_colis or "standard")
        commission, montant_livreur = MatchingService.calculer_commission(prix_final)

        commande.distance_km = distance_km
        commande.duree_estimee_minutes = GeolocationService.estimer_duree_trajet(distance_km)
        commande.prix_propose = prix_final
        commande.commission_plateforme = commission
        commande.montant_livreur = montant_livreur

    await db.commit()
    await db.refresh(commande)

    # ── 3. Créer le paiement GeniusPay si MM et pas déjà créé ───────────
    checkout_url: Optional[str] = commande.geniuspay_checkout_url
    if (
        commande.mode_paiement == ModePaiement.MOBILE_MONEY
        and settings.GENIUSPAY_API_KEY
        and not commande.geniuspay_reference
    ):
        try:
            from ....services import genius_pay_service
            paiement = await genius_pay_service.initier_paiement(
                commande_id=str(commande.id),
                partenaire_id=str(commande.partenaire_id),
                montant=commande.prix_propose,
                description=f"Livraison {commande.numero_commande}",
                nom_client=commande.contact_client_nom,
            )
            commande.geniuspay_reference = paiement.get("reference")
            commande.geniuspay_checkout_url = paiement.get("checkout_url")
            checkout_url = commande.geniuspay_checkout_url
            await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"GeniusPay initier_paiement échoué après partage: {e}")
            # Fallback : on diffuse en Cash-style si le paiement plante
            if commande.status == CommandeStatus.CREEE and partenaire:
                await MatchingService.diffuser_commande(
                    db, commande, partenaire.latitude, partenaire.longitude,
                    partenaire_nom=partenaire.nom,
                )

    # ── 4. Diffuser la commande si Cash et pas déjà diffusée ───────────
    if (
        commande.mode_paiement == ModePaiement.CASH
        and commande.status == CommandeStatus.CREEE
        and partenaire
    ):
        await MatchingService.diffuser_commande(
            db, commande, partenaire.latitude, partenaire.longitude,
            partenaire_nom=partenaire.nom,
        )

    return {
        "message": "Position enregistrée avec succès",
        "already_shared": False,
        "mode_paiement": commande.mode_paiement.value if hasattr(commande.mode_paiement, "value") else commande.mode_paiement,
        "prix": commande.prix_propose,
        "checkout_url": checkout_url,
        "tracking_url": tracking_url,
    }


def _location_html(token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Partager ma position</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f6f6f6; color: #0c0c0c;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 20px;
  }}
  .card {{
    background: #fff; border-radius: 16px; padding: 32px 24px;
    max-width: 400px; width: 100%; text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  }}
  .icon {{ font-size: 48px; margin-bottom: 16px; }}
  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 8px; }}
  p {{ font-size: 14px; color: #6b6b6b; line-height: 1.5; margin-bottom: 24px; }}
  .btn {{
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    background: #0c0c0c; color: #fff; border: none; border-radius: 12px;
    font-size: 16px; font-weight: 600; padding: 16px 32px;
    cursor: pointer; width: 100%; transition: opacity 0.2s;
  }}
  .btn:active {{ opacity: 0.8; }}
  .btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .btn.success {{ background: #05A357; }}
  .btn.error {{ background: #E74C3C; }}
  .spinner {{
    width: 20px; height: 20px; border: 2.5px solid rgba(255,255,255,0.3);
    border-top-color: #fff; border-radius: 50%;
    animation: spin 0.6s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  #status {{ margin-top: 16px; font-size: 13px; color: #6b6b6b; }}
  .price-block {{
    background: #FFF0EB; border-radius: 12px; padding: 18px 16px;
    margin: 0 0 24px 0; border: 1px solid rgba(255, 90, 31, 0.18);
  }}
  .price-label {{
    font-size: 12px; font-weight: 700; color: #D4410A;
    letter-spacing: 1px; text-transform: uppercase;
  }}
  .price-value {{
    font-size: 32px; font-weight: 800; color: #111827;
    letter-spacing: -0.5px; margin-top: 4px;
  }}
  .hint {{
    margin-top: 14px; margin-bottom: 0;
    font-size: 12px; color: #9CA3AF; line-height: 1.5;
  }}
</style>
</head>
<body>
<div class="card" id="card">
  <!-- ÉTAPE 1 : Partage de position -->
  <div id="step-share">
    <div class="icon">📍</div>
    <h1>Partagez votre position</h1>
    <p>Le partenaire a besoin de votre position pour calculer le prix et vous livrer. Appuyez sur le bouton ci-dessous.</p>
    <button class="btn" id="shareBtn" onclick="shareLocation()">
      Partager ma position
    </button>
    <div id="status"></div>
  </div>

  <!-- ÉTAPE 2 : Prix calculé + action (paiement ou suivi) -->
  <div id="step-action" style="display:none;">
    <div class="icon">✅</div>
    <h1>Position enregistrée</h1>
    <div class="price-block" id="priceBlock"></div>
    <button class="btn success" id="actionBtn"></button>
    <p class="hint" id="actionHint"></p>
  </div>
</div>
<script>
function fmtMontant(n) {{
  return Math.round(n).toLocaleString('fr-FR').replace(/,/g, ' ') + ' GNF';
}}

function showActionStep(data) {{
  document.getElementById('step-share').style.display = 'none';
  document.getElementById('step-action').style.display = 'block';

  const priceBlock = document.getElementById('priceBlock');
  const btn = document.getElementById('actionBtn');
  const hint = document.getElementById('actionHint');

  priceBlock.innerHTML = '<div class="price-label">Prix de la livraison</div>'
    + '<div class="price-value">' + fmtMontant(data.prix || 0) + '</div>';

  if (data.mode_paiement === 'MOBILE_MONEY' && data.checkout_url) {{
    btn.textContent = 'Payer maintenant';
    btn.onclick = function() {{ window.location.href = data.checkout_url; }};
    hint.textContent = 'Vous serez redirigé vers la page de paiement Mobile Money.';
  }} else {{
    btn.textContent = 'Suivre ma livraison';
    btn.onclick = function() {{ window.location.href = data.tracking_url; }};
    hint.textContent = 'Le livreur vous appellera pour le règlement en espèces.';
  }}
}}

function shareLocation() {{
  const btn = document.getElementById('shareBtn');
  const st = document.getElementById('status');

  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Localisation en cours...';
  st.textContent = '';

  if (!navigator.geolocation) {{
    btn.innerHTML = 'Géolocalisation non supportée';
    btn.className = 'btn error';
    st.textContent = 'Votre navigateur ne supporte pas la géolocalisation.';
    return;
  }}

  navigator.geolocation.getCurrentPosition(
    function(pos) {{
      btn.innerHTML = '<div class="spinner"></div> Calcul du prix...';
      fetch('/loc/{token}/submit', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude
        }})
      }})
      .then(r => r.json())
      .then(data => {{
        showActionStep(data);
      }})
      .catch(err => {{
        btn.innerHTML = 'Réessayer';
        btn.className = 'btn error';
        btn.disabled = false;
        st.textContent = 'Erreur d\\'envoi. Veuillez réessayer.';
      }});
    }},
    function(err) {{
      btn.disabled = false;
      btn.innerHTML = 'Réessayer';
      btn.className = 'btn error';
      if (err.code === 1) {{
        st.textContent = 'Vous avez refusé l\\'accès à votre position. Autorisez-la dans les paramètres de votre navigateur.';
      }} else {{
        st.textContent = 'Impossible d\\'obtenir votre position. Vérifiez que le GPS est activé.';
      }}
    }},
    {{ enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }}
  );
}}
</script>
</body>
</html>"""


def _already_shared_html() -> str:
    return """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Position déjà partagée</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f6f6f6; color: #0c0c0c;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 20px;
  }
  .card {
    background: #fff; border-radius: 16px; padding: 32px 24px;
    max-width: 400px; width: 100%; text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  }
  .icon { font-size: 48px; margin-bottom: 16px; }
  h1 { font-size: 20px; font-weight: 700; margin-bottom: 8px; }
  p { font-size: 14px; color: #6b6b6b; line-height: 1.5; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h1>Position déjà partagée</h1>
  <p>Votre position a déjà été enregistrée. Le livreur est en route !</p>
</div>
</body>
</html>"""


def _error_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Erreur</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f6f6f6; color: #0c0c0c;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 20px;
  }}
  .card {{
    background: #fff; border-radius: 16px; padding: 32px 24px;
    max-width: 400px; width: 100%; text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  }}
  .icon {{ font-size: 48px; margin-bottom: 16px; }}
  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 8px; color: #E74C3C; }}
  p {{ font-size: 14px; color: #6b6b6b; line-height: 1.5; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">❌</div>
  <h1>{message}</h1>
  <p>Ce lien n'est plus valide. Contactez le partenaire pour obtenir un nouveau lien.</p>
</div>
</body>
</html>"""
