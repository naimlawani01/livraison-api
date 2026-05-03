"""
Suivi de livraison public pour le client.

Le client reçoit un lien /suivi/{token} par WhatsApp.
La page affiche les étapes de la livraison + une carte Leaflet en temps réel
(position du livreur lue dans Redis GEO).
"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....core.redis import redis_client
from ....models.commande import Commande
from ....models.livreur import Livreur
from ....models.partenaire import Partenaire
from ....models.user import User
from ....utils.dependencies import get_current_user

logger = logging.getLogger(__name__)
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
        token = secrets.token_hex(10)
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

    partenaire_q = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
    partenaire_r = await db.execute(partenaire_q)
    partenaire_row = partenaire_r.scalar_one_or_none()
    partenaire_nom = partenaire_row.nom if partenaire_row else "Partenaire"

    return HTMLResponse(content=_tracking_html(token, commande.numero_commande, partenaire_nom))


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
    livreur_pos = None
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

            # Position temps réel : on tente Redis GEO d'abord (plus frais),
            # puis on retombe sur les coordonnées Postgres.
            try:
                redis_pos = await redis_client.geopos(
                    "livreurs_locations", str(livreur.id)
                )
                if redis_pos and redis_pos[0]:
                    lng, lat = redis_pos[0]
                    livreur_pos = {"latitude": float(lat), "longitude": float(lng)}
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis geopos indispo: %s", exc)
            if livreur_pos is None and livreur.latitude and livreur.longitude:
                livreur_pos = {
                    "latitude": float(livreur.latitude),
                    "longitude": float(livreur.longitude),
                }

    # Position partenaire (point de départ)
    partenaire_pos = None
    partenaire_q = select(Partenaire).where(Partenaire.id == commande.partenaire_id)
    partenaire_r = await db.execute(partenaire_q)
    partenaire_row = partenaire_r.scalar_one_or_none()
    if partenaire_row and partenaire_row.latitude and partenaire_row.longitude:
        partenaire_pos = {
            "latitude": float(partenaire_row.latitude),
            "longitude": float(partenaire_row.longitude),
        }

    # Position client (point d'arrivée)
    client_pos = None
    if commande.latitude_client is not None and commande.longitude_client is not None:
        client_pos = {
            "latitude": float(commande.latitude_client),
            "longitude": float(commande.longitude_client),
        }

    return {
        "status": commande.status.value if hasattr(commande.status, 'value') else commande.status,
        "numero_commande": commande.numero_commande,
        "livreur_nom": livreur_nom,
        "livreur_telephone": livreur_tel,
        "livreur_position": livreur_pos,
        "partenaire_position": partenaire_pos,
        "client_position": client_pos,
        "created_at": commande.created_at.isoformat() if commande.created_at else None,
        "acceptee_at": commande.acceptee_at.isoformat() if commande.acceptee_at else None,
        "recuperee_at": commande.recuperee_at.isoformat() if commande.recuperee_at else None,
        "livree_at": commande.livree_at.isoformat() if commande.livree_at else None,
        "annulee_at": commande.annulee_at.isoformat() if commande.annulee_at else None,
    }


# ── HTML Templates ──────────────────────────────────────

def _tracking_html(token: str, numero: str, partenaire: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Suivi commande {numero}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f6f6f6; color: #0c0c0c;
    min-height: 100vh; padding: 20px 20px 40px;
  }}
  .card {{
    background: #fff; border-radius: 16px; padding: 24px 22px;
    max-width: 460px; margin: 0 auto;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  }}
  .header {{ text-align: center; margin-bottom: 22px; }}
  .header .icon {{ font-size: 36px; margin-bottom: 10px; }}
  .header h1 {{ font-size: 18px; font-weight: 700; }}
  .header .sub {{ font-size: 13px; color: #6b6b6b; margin-top: 4px; }}
  .header .numero {{ font-size: 12px; color: #999; margin-top: 2px; font-family: monospace; }}

  /* Map */
  #map {{
    height: 280px; border-radius: 14px; overflow: hidden;
    margin-bottom: 22px; background: #e5e7eb;
  }}
  #mapEmpty {{
    height: 280px; border-radius: 14px;
    display: flex; align-items: center; justify-content: center;
    background: #f3f4f6; color: #9ca3af; font-size: 13px;
    margin-bottom: 22px; text-align: center; padding: 0 24px;
  }}
  .leaflet-control-attribution {{ font-size: 9px !important; }}

  /* Marker custom */
  .marker-pin {{
    width: 36px; height: 36px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.25);
    border: 2px solid #fff;
  }}
  .marker-livreur {{ background: #FF5A1F; animation: pulseRing 2.2s ease-out infinite; }}
  .marker-partenaire {{ background: #111827; }}
  .marker-client {{ background: #059669; }}
  @keyframes pulseRing {{
    0% {{ box-shadow: 0 0 0 0 rgba(255,90,31,0.55); }}
    70% {{ box-shadow: 0 0 0 14px rgba(255,90,31,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(255,90,31,0); }}
  }}

  /* Stepper */
  .stepper {{ position: relative; padding-left: 32px; }}
  .step {{
    position: relative; padding-bottom: 22px;
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

  .step-title {{ font-size: 14px; font-weight: 600; margin-bottom: 2px; }}
  .step-desc {{ font-size: 12px; color: #6b6b6b; }}
  .step-time {{ font-size: 11px; color: #999; margin-top: 2px; }}

  /* Livreur card */
  .livreur-card {{
    margin-top: 20px; padding: 14px; border-radius: 12px;
    background: #f0faf4; border: 1px solid #d0eedd;
    display: none;
  }}
  .livreur-card.show {{ display: block; }}
  .livreur-card .name {{ font-size: 14px; font-weight: 600; }}
  .livreur-card .phone {{
    display: inline-block; margin-top: 8px; padding: 8px 16px;
    background: #05A357; color: #fff; border-radius: 8px;
    text-decoration: none; font-size: 14px; font-weight: 600;
  }}

  /* Status banner */
  .banner {{
    text-align: center; margin-top: 18px; padding: 12px;
    border-radius: 10px; font-size: 13px; font-weight: 600;
    display: none;
  }}
  .banner.cancelled {{ display: block; background: #f8d7da; color: #721c24; }}

  .pulse {{ animation: pulse 2s ease-in-out infinite; }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
  }}
  .refresh-info {{
    text-align: center; margin-top: 14px;
    font-size: 11px; color: #bbb;
  }}
</style>
</head>
<body>

<div class="card">
  <div class="header">
    <div class="icon">🛵</div>
    <h1>{partenaire}</h1>
    <div class="sub">Suivi de votre livraison</div>
    <div class="numero">{numero}</div>
  </div>

  <div id="map"></div>
  <div id="mapEmpty" style="display:none;">📍 La position du livreur s'affichera ici dès qu'un livreur acceptera la course.</div>

  <div class="stepper">
    <div class="step" id="step-creee">
      <div class="step-title">Commande reçue</div>
      <div class="step-desc">Le partenaire prépare votre commande</div>
      <div class="step-time" id="time-creee"></div>
    </div>
    <div class="step" id="step-acceptee">
      <div class="step-title">Livreur assigné</div>
      <div class="step-desc">Un livreur se dirige vers le partenaire</div>
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

  <div class="banner cancelled" id="bannerCancelled">❌ Cette commande a été annulée</div>

  <div class="refresh-info pulse" id="refreshInfo">Mise à jour automatique…</div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<script>
const TOKEN = '{token}';

const STEP_MAP = {{
  'CREEE': 'step-creee', 'DIFFUSEE': 'step-creee',
  'ACCEPTEE': 'step-acceptee',
  'EN_RECUPERATION': 'step-recuperee',
  'EN_LIVRAISON': 'step-livraison',
  'TERMINEE': 'step-terminee'
}};

let map = null;
let livreurMarker = null;
let partenaireMarker = null;
let clientMarker = null;
let mapInitialized = false;

function makeIcon(cls, emoji) {{
  return L.divIcon({{
    className: '',
    html: '<div class="marker-pin ' + cls + '">' + emoji + '</div>',
    iconSize: [36, 36],
    iconAnchor: [18, 18],
  }});
}}

function ensureMap() {{
  if (mapInitialized) return;
  mapInitialized = true;
  document.getElementById('mapEmpty').style.display = 'none';
  document.getElementById('map').style.display = 'block';
  map = L.map('map', {{
    zoomControl: false,
    attributionControl: true,
    scrollWheelZoom: false,
  }}).setView([9.535, -13.677], 13); // Conakry par défaut
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '© OpenStreetMap',
  }}).addTo(map);
}}

function updateMap(data) {{
  const liv = data.livreur_position;
  const part = data.partenaire_position;
  const cli = data.client_position;

  // Si on n'a aucune position du tout → on garde le placeholder
  if (!liv && !part && !cli) {{
    document.getElementById('map').style.display = 'none';
    document.getElementById('mapEmpty').style.display = 'flex';
    return;
  }}

  ensureMap();

  // Partenaire (départ) — fixe
  if (part && !partenaireMarker) {{
    partenaireMarker = L.marker([part.latitude, part.longitude], {{
      icon: makeIcon('marker-partenaire', '🏪'),
      title: 'Partenaire',
    }}).addTo(map);
  }}

  // Client (arrivée) — fixe quand connue
  if (cli && !clientMarker) {{
    clientMarker = L.marker([cli.latitude, cli.longitude], {{
      icon: makeIcon('marker-client', '🏠'),
      title: 'Vous',
    }}).addTo(map);
  }}

  // Livreur — animé
  if (liv) {{
    const latlng = [liv.latitude, liv.longitude];
    if (!livreurMarker) {{
      livreurMarker = L.marker(latlng, {{
        icon: makeIcon('marker-livreur', '🛵'),
        title: 'Livreur',
      }}).addTo(map);
    }} else {{
      livreurMarker.setLatLng(latlng);
    }}
  }}

  // Auto-fit bounds sur les markers actifs
  const points = [];
  if (liv) points.push([liv.latitude, liv.longitude]);
  if (part) points.push([part.latitude, part.longitude]);
  if (cli) points.push([cli.latitude, cli.longitude]);
  if (points.length === 1) {{
    map.setView(points[0], 15);
  }} else if (points.length > 1) {{
    map.fitBounds(points, {{ padding: [40, 40], maxZoom: 16 }});
  }}
}}

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
    stepIds.forEach(id => {{ document.getElementById(id).className = 'step'; }});
    document.getElementById('step-creee').className = 'step cancelled';
    document.getElementById('bannerCancelled').style.display = 'block';
    document.getElementById('refreshInfo').style.display = 'none';
    return;
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

  if (data.created_at) document.getElementById('time-creee').textContent = fmtTime(data.created_at);
  if (data.acceptee_at) document.getElementById('time-acceptee').textContent = fmtTime(data.acceptee_at);
  if (data.recuperee_at) document.getElementById('time-recuperee').textContent = fmtTime(data.recuperee_at);
  if (data.livree_at) document.getElementById('time-livree').textContent = fmtTime(data.livree_at);

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

  document.getElementById('bannerCancelled').style.display = s === 'ANNULEE' ? 'block' : 'none';

  if (s === 'TERMINEE' || s === 'ANNULEE') {{
    document.getElementById('refreshInfo').style.display = 'none';
  }}

  updateMap(data);
}}

function poll() {{
  fetch('/suivi/' + TOKEN + '/status')
    .then(r => r.json())
    .then(data => {{
      updateUI(data);
      if (data.status !== 'TERMINEE' && data.status !== 'ANNULEE') {{
        setTimeout(poll, 5000);
      }}
    }})
    .catch(() => setTimeout(poll, 10000));
}}

// État initial : on cache la map tant qu'on n'a pas reçu de position
document.getElementById('map').style.display = 'none';
document.getElementById('mapEmpty').style.display = 'flex';
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
  <p>Ce lien n'est plus valide. Contactez le partenaire.</p>
</div>
</body>
</html>"""
