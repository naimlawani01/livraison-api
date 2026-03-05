"""
Endpoints pour le partage de localisation client.

Flux :
1. Le restaurant crée une commande → un location_token est généré
2. Le restaurant envoie le lien (contenant le token) au client via WhatsApp/SMS
3. Le client ouvre le lien → page HTML qui demande sa position GPS
4. Le client autorise → sa position est envoyée ici et sauvegardée
"""
import secrets
from datetime import datetime
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
    """Page HTML publique pour que le client partage sa localisation"""
    query = select(Commande).where(Commande.location_token == token)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        return HTMLResponse(content=_error_html("Lien invalide ou expiré"), status_code=404)

    if commande.location_shared_at:
        return HTMLResponse(content=_already_shared_html())

    return HTMLResponse(content=_location_html(token))


@router.post("/loc/{token}/submit")
async def submit_location(token: str, data: LocationSubmit, db: AsyncSession = Depends(get_db)):
    """Recevoir la position GPS du client (endpoint public, pas d'auth)"""
    query = select(Commande).where(Commande.location_token == token)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        raise HTTPException(status_code=404, detail="Lien invalide")

    if commande.location_shared_at:
        return {"message": "Position déjà enregistrée", "already_shared": True}

    commande.latitude_client = data.latitude
    commande.longitude_client = data.longitude
    commande.location_shared_at = datetime.utcnow()
    await db.commit()

    return {"message": "Position enregistrée avec succès", "already_shared": False}


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
</style>
</head>
<body>
<div class="card">
  <div class="icon">📍</div>
  <h1>Partagez votre position</h1>
  <p>Le restaurant a besoin de votre position pour vous livrer. Appuyez sur le bouton ci-dessous pour partager votre localisation GPS.</p>
  <button class="btn" id="shareBtn" onclick="shareLocation()">
    Partager ma position
  </button>
  <div id="status"></div>
</div>
<script>
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
      btn.innerHTML = '<div class="spinner"></div> Envoi en cours...';
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
        btn.innerHTML = '✓ Position envoyée !';
        btn.className = 'btn success';
        st.textContent = 'Merci ! Le livreur connaît maintenant votre position.';
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
  <p>Ce lien n'est plus valide. Contactez le restaurant pour obtenir un nouveau lien.</p>
</div>
</body>
</html>"""
