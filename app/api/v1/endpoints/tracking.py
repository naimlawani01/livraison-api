"""
Suivi de livraison public pour le client.

Le client reçoit un lien /suivi/{token} par WhatsApp.
La page affiche les étapes de la livraison et se met à jour automatiquement.
"""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from ....core.database import get_db
from ....models.commande import Commande
from ....models.restaurant import Restaurant
from ....models.livreur import Livreur
from ....models.user import User
from ....utils.dependencies import get_current_user

router = APIRouter()


class TrackingLinkResponse(BaseModel):
    tracking_token: str
    link: str


@router.post("/commandes/{commande_id}/tracking-link", response_model=TrackingLinkResponse)
async def generate_tracking_link(
    commande_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Commande).where(Commande.id == commande_id)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        raise HTTPException(status_code=404, detail="Commande non trouvée")

    if commande.tracking_token:
        token = commande.tracking_token
    else:
        token = secrets.token_urlsafe(32)
        commande.tracking_token = token
        await db.commit()

    return TrackingLinkResponse(tracking_token=token, link=f"/suivi/{token}")


# ── Page HTML publique ──────────────────────────────────
@router.get("/suivi/{token}", response_class=HTMLResponse)
async def tracking_page(token: str, db: AsyncSession = Depends(get_db)):
    query = select(Commande).where(Commande.tracking_token == token)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        return HTMLResponse(content=_error_html("Lien invalide ou expiré"), status_code=404)

    resto_q = select(Restaurant).where(Restaurant.id == commande.restaurant_id)
    resto_r = await db.execute(resto_q)
    restaurant = resto_r.scalar_one_or_none()
    resto_nom = restaurant.nom if restaurant else "Restaurant"

    return HTMLResponse(content=_tracking_html(token, commande.numero_commande, resto_nom))


# ── API JSON pour le polling ────────────────────────────
@router.get("/suivi/{token}/status")
async def tracking_status(token: str, db: AsyncSession = Depends(get_db)):
    query = select(Commande).where(Commande.tracking_token == token)
    result = await db.execute(query)
    commande = result.scalar_one_or_none()

    if not commande:
        raise HTTPException(status_code=404, detail="Lien invalide")

    livreur_nom = None
    livreur_tel = None
    if commande.livreur_id:
        liv_q = select(Livreur).where(Livreur.id == commande.livreur_id)
        liv_r = await db.execute(liv_q)
        livreur = liv_r.scalar_one_or_none()
        if livreur:
            livreur_nom = livreur.nom_complet
            user_q = select(User).where(User.id == livreur.user_id)
            user_r = await db.execute(user_q)
            user = user_r.scalar_one_or_none()
            if user:
                livreur_tel = user.phone

    return {
        "status": commande.status.value if hasattr(commande.status, 'value') else commande.status,
        "numero_commande": commande.numero_commande,
        "livreur_nom": livreur_nom,
        "livreur_telephone": livreur_tel,
        "created_at": commande.created_at.isoformat() if commande.created_at else None,
        "acceptee_at": commande.acceptee_at.isoformat() if commande.acceptee_at else None,
        "recuperee_at": commande.recuperee_at.isoformat() if commande.recuperee_at else None,
        "livree_at": commande.livree_at.isoformat() if commande.livree_at else None,
        "annulee_at": commande.annulee_at.isoformat() if commande.annulee_at else None,
    }


# ── HTML Templates ──────────────────────────────────────

def _tracking_html(token: str, numero: str, restaurant: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Suivi commande {numero}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f6f6f6; color: #0c0c0c;
    min-height: 100vh; padding: 20px 20px 40px;
  }}
  .card {{
    background: #fff; border-radius: 16px; padding: 28px 24px;
    max-width: 440px; margin: 0 auto;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  }}
  .header {{ text-align: center; margin-bottom: 28px; }}
  .header .icon {{ font-size: 40px; margin-bottom: 12px; }}
  .header h1 {{ font-size: 18px; font-weight: 700; }}
  .header .sub {{ font-size: 13px; color: #6b6b6b; margin-top: 4px; }}
  .header .numero {{ font-size: 12px; color: #999; margin-top: 2px; font-family: monospace; }}

  /* Stepper */
  .stepper {{ position: relative; padding-left: 32px; }}
  .step {{
    position: relative; padding-bottom: 28px;
    opacity: 0.35; transition: opacity 0.4s;
  }}
  .step.active {{ opacity: 1; }}
  .step.done {{ opacity: 0.7; }}
  .step:last-child {{ padding-bottom: 0; }}

  .step::before {{
    content: ''; position: absolute; left: -25px; top: 6px;
    width: 12px; height: 12px; border-radius: 50%;
    background: #ddd; border: 2px solid #ddd; transition: all 0.4s;
  }}
  .step.active::before {{ background: #0c0c0c; border-color: #0c0c0c; box-shadow: 0 0 0 4px rgba(12,12,12,0.15); }}
  .step.done::before {{ background: #05A357; border-color: #05A357; }}
  .step.cancelled::before {{ background: #E74C3C; border-color: #E74C3C; }}

  .step::after {{
    content: ''; position: absolute; left: -20px; top: 22px;
    width: 2px; height: calc(100% - 16px); background: #eee; transition: background 0.4s;
  }}
  .step:last-child::after {{ display: none; }}
  .step.done::after {{ background: #05A357; }}

  .step-title {{ font-size: 15px; font-weight: 600; margin-bottom: 2px; }}
  .step-desc {{ font-size: 12px; color: #6b6b6b; }}
  .step-time {{ font-size: 11px; color: #999; margin-top: 2px; }}

  /* Livreur card */
  .livreur-card {{
    margin-top: 24px; padding: 16px; border-radius: 12px;
    background: #f0faf4; border: 1px solid #d0eedd;
    display: none;
  }}
  .livreur-card.show {{ display: block; }}
  .livreur-card .name {{ font-size: 15px; font-weight: 600; }}
  .livreur-card .phone {{
    display: inline-block; margin-top: 8px; padding: 8px 16px;
    background: #05A357; color: #fff; border-radius: 8px;
    text-decoration: none; font-size: 14px; font-weight: 600;
  }}

  /* Status banner */
  .banner {{
    text-align: center; margin-top: 20px; padding: 12px;
    border-radius: 10px; font-size: 13px; font-weight: 600;
    display: none;
  }}
  .banner.delivered {{ display: block; background: #d4edda; color: #155724; }}
  .banner.cancelled {{ display: block; background: #f8d7da; color: #721c24; }}

  .pulse {{ animation: pulse 2s ease-in-out infinite; }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
  }}
  .refresh-info {{
    text-align: center; margin-top: 16px;
    font-size: 11px; color: #bbb;
  }}
</style>
</head>
<body>

<div class="card">
  <div class="header">
    <div class="icon">🛵</div>
    <h1>{restaurant}</h1>
    <div class="sub">Suivi de votre livraison</div>
    <div class="numero">{numero}</div>
  </div>

  <div class="stepper">
    <div class="step" id="step-creee">
      <div class="step-title">Commande reçue</div>
      <div class="step-desc">Le restaurant prépare votre commande</div>
      <div class="step-time" id="time-creee"></div>
    </div>
    <div class="step" id="step-acceptee">
      <div class="step-title">Livreur assigné</div>
      <div class="step-desc">Un livreur se dirige vers le restaurant</div>
      <div class="step-time" id="time-acceptee"></div>
    </div>
    <div class="step" id="step-recuperee">
      <div class="step-title">Commande récupérée</div>
      <div class="step-desc">Le livreur a récupéré votre commande</div>
      <div class="step-time" id="time-recuperee"></div>
    </div>
    <div class="step" id="step-livraison">
      <div class="step-title">En route vers vous</div>
      <div class="step-desc">Le livreur est en chemin !</div>
    </div>
    <div class="step" id="step-terminee">
      <div class="step-title">Livrée !</div>
      <div class="step-desc">Bon appétit !</div>
      <div class="step-time" id="time-livree"></div>
    </div>
  </div>

  <div class="livreur-card" id="livreurCard">
    <div class="name" id="livreurNom"></div>
    <a class="phone" id="livreurTel" href="#">📞 Appeler le livreur</a>
  </div>

  <div class="banner delivered" id="bannerDelivered">✅ Votre commande a été livrée !</div>
  <div class="banner cancelled" id="bannerCancelled">❌ Cette commande a été annulée</div>

  <div class="refresh-info pulse" id="refreshInfo">Mise à jour automatique…</div>
</div>

<script>
const STEPS_ORDER = ['CREEE','DIFFUSEE','ACCEPTEE','EN_RECUPERATION','EN_LIVRAISON','TERMINEE'];
const STEP_MAP = {{
  'CREEE': 'step-creee', 'DIFFUSEE': 'step-creee',
  'ACCEPTEE': 'step-acceptee',
  'EN_RECUPERATION': 'step-recuperee',
  'EN_LIVRAISON': 'step-livraison',
  'TERMINEE': 'step-terminee'
}};

function fmtTime(iso) {{
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('fr-FR', {{ hour: '2-digit', minute: '2-digit' }});
}}

function updateUI(data) {{
  const s = data.status;
  const stepIds = ['step-creee','step-acceptee','step-recuperee','step-livraison','step-terminee'];
  const currentStepId = STEP_MAP[s];

  if (s === 'ANNULEE') {{
    stepIds.forEach(id => {{
      document.getElementById(id).className = 'step';
    }});
    document.getElementById('step-creee').className = 'step cancelled';
    document.getElementById('bannerCancelled').style.display = 'block';
    document.getElementById('refreshInfo').style.display = 'none';
    return;
  }}

  let reached = false;
  for (let i = stepIds.length - 1; i >= 0; i--) {{
    if (stepIds[i] === currentStepId) reached = true;
  }}

  let pastCurrent = false;
  for (const id of stepIds) {{
    const el = document.getElementById(id);
    if (id === currentStepId) {{
      el.className = 'step active';
      pastCurrent = true;
    }} else if (!pastCurrent) {{
      el.className = 'step done';
    }} else {{
      el.className = 'step';
    }}
  }}

  // Times
  if (data.created_at) document.getElementById('time-creee').textContent = fmtTime(data.created_at);
  if (data.acceptee_at) document.getElementById('time-acceptee').textContent = fmtTime(data.acceptee_at);
  if (data.recuperee_at) document.getElementById('time-recuperee').textContent = fmtTime(data.recuperee_at);
  if (data.livree_at) document.getElementById('time-livree').textContent = fmtTime(data.livree_at);

  // Livreur
  const lc = document.getElementById('livreurCard');
  if (data.livreur_nom) {{
    lc.className = 'livreur-card show';
    document.getElementById('livreurNom').textContent = '🏍️ ' + data.livreur_nom;
    if (data.livreur_telephone) {{
      const tel = document.getElementById('livreurTel');
      tel.href = 'tel:' + data.livreur_telephone;
      tel.style.display = 'inline-block';
    }}
  }} else {{
    lc.className = 'livreur-card';
  }}

  // Banners
  document.getElementById('bannerDelivered').style.display = s === 'TERMINEE' ? 'block' : 'none';
  document.getElementById('bannerCancelled').style.display = s === 'ANNULEE' ? 'block' : 'none';

  if (s === 'TERMINEE' || s === 'ANNULEE') {{
    document.getElementById('refreshInfo').style.display = 'none';
  }}
}}

function poll() {{
  fetch('/suivi/{token}/status')
    .then(r => r.json())
    .then(data => {{
      updateUI(data);
      if (data.status !== 'TERMINEE' && data.status !== 'ANNULEE') {{
        setTimeout(poll, 5000);
      }}
    }})
    .catch(() => setTimeout(poll, 10000));
}}

poll();
</script>
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
  <p>Ce lien n'est plus valide. Contactez le restaurant.</p>
</div>
</body>
</html>"""
