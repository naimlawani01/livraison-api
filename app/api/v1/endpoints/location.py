"""
Endpoints pour le partage de localisation client.

Flux :
1. Le expediteur crée une course → un location_token est généré
2. Le expediteur envoie le lien (contenant le token) au client via WhatsApp/SMS
3. Le client ouvre le lien → page HTML qui demande sa position GPS
4. Le client autorise → sa position est envoyée ici et sauvegardée
"""
import html as _html
import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from ....core.rate_limit import limiter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field

from ....core.database import get_db
from ....models.course import Course
from ....models.expediteur import Expediteur
from ....utils.dependencies import get_current_expediteur

router = APIRouter()


class LocationSubmit(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class GenerateLocationLinkResponse(BaseModel):
    location_token: str
    link: str


@router.post("/courses/{course_id}/location-link", response_model=GenerateLocationLinkResponse)
async def generate_location_link(
    course_id: str,
    expediteur: Expediteur = Depends(get_current_expediteur),
    db: AsyncSession = Depends(get_db)
):
    """Générer un lien de localisation pour une course"""
    query = select(Course).where(Course.id == course_id)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Course non trouvée")

    if str(course.expediteur_id) != str(expediteur.id):
        raise HTTPException(status_code=403, detail="Accès non autorisé")

    if course.location_token:
        token = course.location_token
    else:
        token = secrets.token_urlsafe(32)
        course.location_token = token
        await db.commit()

    return GenerateLocationLinkResponse(
        location_token=token,
        link=f"/loc/{token}"
    )


@router.get("/loc/{token}", response_class=HTMLResponse)
async def location_page(token: str, db: AsyncSession = Depends(get_db)):
    """Page HTML publique : partage de position → prix → paiement / tracking."""
    query = select(Course).where(Course.location_token == token)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if not course:
        return HTMLResponse(content=_error_html("Lien invalide ou expiré"), status_code=404)

    # Position déjà partagée → on redirige vers la suite naturelle
    # • MM avec checkout_url disponible et paiement non confirmé → page paiement
    # • Sinon → page de tracking
    if course.location_shared_at:
        from fastapi.responses import RedirectResponse
        from ....models.course import ModePaiement

        if (
            course.mode_paiement == ModePaiement.MOBILE_MONEY
            and course.geniuspay_checkout_url
            and course.paiement_confirme != "oui"
        ):
            return RedirectResponse(url=course.geniuspay_checkout_url, status_code=302)
        if course.tracking_token:
            return RedirectResponse(url=f"/suivi/{course.tracking_token}", status_code=302)
        return HTMLResponse(content=_already_shared_html())

    return HTMLResponse(content=_location_html(token))


@router.post("/loc/{token}/submit")
@limiter.limit("20/minute")
async def submit_location(
    request: Request,
    token: str,
    data: LocationSubmit,
    db: AsyncSession = Depends(get_db),
):
    """Recevoir la position GPS du client (endpoint public, pas d'auth).

    Cet endpoint déclenche aussi :
      1. Le calcul du prix final (distance × M_colis × M_heure)
      2. La création du lien de paiement GeniusPay (si Mobile Money)
      3. La diffusion de la course aux livreurs (si Cash, ou MM en fallback)

    Retourne les URLs nécessaires à la page web pour orienter le client :
    bouton "Payer" (MM) ou "Suivre" (Cash).
    """
    import logging
    from ....core.config import settings
    from ....models.course import CourseStatus, ModePaiement
    from ....models.expediteur import Expediteur
    from ....services.geolocation_service import GeolocationService
    from ....services.matching_service import MatchingService

    logger = logging.getLogger(__name__)

    query = select(Course).where(Course.location_token == token)
    result = await db.execute(query)
    course: Optional[Course] = result.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Lien invalide")

    # Si déjà partagée, on retourne juste les URLs pour rediriger
    tracking_url = f"{settings.PUBLIC_BASE_URL}/suivi/{course.tracking_token}"

    if course.location_shared_at:
        return {
            "message": "Position déjà enregistrée",
            "already_shared": True,
            "mode_paiement": course.mode_paiement.value if hasattr(course.mode_paiement, "value") else course.mode_paiement,
            "prix": course.prix_propose,
            "checkout_url": course.geniuspay_checkout_url,
            "tracking_url": tracking_url,
        }

    # ── 1. Sauvegarder la position ────────────────────────────────────────
    course.latitude_client = data.latitude
    course.longitude_client = data.longitude
    course.location_shared_at = datetime.now(timezone.utc)

    # ── 2. Recalculer le prix selon distance + nature_colis ──────────────
    expediteur_q = select(Expediteur).where(Expediteur.id == course.expediteur_id)
    expediteur_r = await db.execute(expediteur_q)
    expediteur: Optional[Expediteur] = expediteur_r.scalar_one_or_none()

    if expediteur and expediteur.latitude and expediteur.longitude:
        distance_km = GeolocationService.calculer_distance(
            (expediteur.latitude, expediteur.longitude),
            (data.latitude, data.longitude),
        )
        from ....services import pricing, credit_service
        ancienne_commission = course.commission_plateforme or 0.0
        tarif = pricing.calculer_tarif(distance_km, course.nature_colis or "standard")

        course.distance_km = distance_km
        course.duree_estimee_minutes = GeolocationService.estimer_duree_trajet(distance_km)
        course.prix_propose = tarif.prix
        course.commission_plateforme = tarif.commission
        course.montant_livreur = tarif.gain_livreur
        await db.flush()

        # Ajuster le Crédit de l'expéditeur du delta de commission (courses CASH,
        # dont la commission a été réservée au plancher à la création).
        if course.mode_paiement == ModePaiement.CASH:
            await credit_service.ajuster_commission(
                db, course.expediteur_id, ancienne_commission, tarif.commission,
                course_id=course.id,
            )

    await db.commit()
    await db.refresh(course)

    # ── 3. Créer le paiement GeniusPay si MM et pas déjà créé ───────────
    checkout_url: Optional[str] = course.geniuspay_checkout_url
    if (
        course.mode_paiement == ModePaiement.MOBILE_MONEY
        and settings.GENIUSPAY_API_KEY
        and not course.geniuspay_reference
    ):
        try:
            from ....services import genius_pay_service
            paiement = await genius_pay_service.initier_paiement(
                course_id=str(course.id),
                expediteur_id=str(course.expediteur_id),
                montant=course.prix_propose,
                description=f"Livraison {course.numero_course}",
                nom_client=course.contact_client_nom,
            )
            course.geniuspay_reference = paiement.get("reference")
            course.geniuspay_checkout_url = paiement.get("checkout_url")
            checkout_url = course.geniuspay_checkout_url
            await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"GeniusPay initier_paiement échoué après partage: {e}")
            # Fallback : on diffuse en Cash-style si le paiement plante
            if course.status == CourseStatus.CREEE and expediteur:
                await MatchingService.diffuser_course(
                    db, course, expediteur.latitude, expediteur.longitude,
                    expediteur_nom=expediteur.nom,
                )

    # ── 4. Diffuser la course si Cash et pas déjà diffusée ───────────
    if (
        course.mode_paiement == ModePaiement.CASH
        and course.status == CourseStatus.CREEE
        and expediteur
    ):
        await MatchingService.diffuser_course(
            db, course, expediteur.latitude, expediteur.longitude,
            expediteur_nom=expediteur.nom,
        )

    return {
        "message": "Position enregistrée avec succès",
        "already_shared": False,
        "mode_paiement": course.mode_paiement.value if hasattr(course.mode_paiement, "value") else course.mode_paiement,
        "prix": course.prix_propose,
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
    <p>Le expediteur a besoin de votre position pour calculer le prix et vous livrer. Appuyez sur le bouton ci-dessous.</p>
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
    safe_message = _html.escape(message)
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
  <h1>{safe_message}</h1>
  <p>Ce lien n'est plus valide. Contactez le expediteur pour obtenir un nouveau lien.</p>
</div>
</body>
</html>"""
