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
    livreur_photo = None
    livreur_vehicule = None
    livreur_note = None
    if commande.livreur_id:
        liv_q = select(Livreur).where(Livreur.id == commande.livreur_id)
        liv_r = await db.execute(liv_q)
        livreur = liv_r.scalar_one_or_none()
        if livreur:
            livreur_nom = livreur.nom_complet
            livreur_photo = livreur.photo_profil_url
            livreur_vehicule = livreur.type_vehicule
            livreur_note = float(livreur.note_moyenne) if livreur.note_moyenne else None
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
        "client_nom": commande.contact_client_nom,
        "adresse_client": commande.adresse_client,
        "livreur_nom": livreur_nom,
        "livreur_telephone": livreur_tel,
        "livreur_position": livreur_pos,
        "livreur_photo": livreur_photo,
        "livreur_vehicule": livreur_vehicule,
        "livreur_note": livreur_note,
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
    import json as _json
    partenaire_js = _json.dumps(partenaire)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="theme-color" content="#0c0c0c">
<title>Suivi de votre livraison • {partenaire}</title>
<meta property="og:title" content="Suivi en direct de votre livraison">
<meta property="og:description" content="Suivez votre livreur en temps réel sur la carte. Sönaiyaa.">
<meta property="og:type" content="website">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous">
<style>
  :root {{
    --brand: #FF5A1F;
    --brand-dark: #E84A12;
    --ink: #0c0c0c;
    --ink-soft: #4b5563;
    --ink-muted: #9ca3af;
    --bg: #f6f6f6;
    --surface: #ffffff;
    --line: rgba(0,0,0,0.06);
    --green: #05A357;
    --red: #E74C3C;
    --shadow: 0 -8px 32px rgba(0,0,0,0.12);
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  html, body {{
    height: 100%; overflow: hidden;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    color: var(--ink); background: var(--bg);
    -webkit-font-smoothing: antialiased;
  }}

  /* ── Map fullscreen ───────────────────────────────────── */
  #map {{
    position: fixed; inset: 0;
    z-index: 1; background: #e5e7eb;
  }}
  #mapEmpty {{
    position: fixed; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    z-index: 2; background: linear-gradient(135deg, #fef3eb 0%, #ffffff 100%);
    text-align: center; padding: 0 32px;
  }}
  #mapEmpty .icon {{
    font-size: 64px; margin-bottom: 18px;
    animation: float 3s ease-in-out infinite;
  }}
  #mapEmpty h2 {{ font-size: 20px; font-weight: 700; margin-bottom: 8px; }}
  #mapEmpty p {{ font-size: 14px; color: var(--ink-soft); max-width: 280px; line-height: 1.5; }}
  @keyframes float {{
    0%, 100% {{ transform: translateY(0); }}
    50% {{ transform: translateY(-10px); }}
  }}
  .leaflet-control-attribution {{
    font-size: 9px !important; opacity: 0.6;
    background: rgba(255,255,255,0.8) !important;
  }}

  /* ── Top bar flottant ─────────────────────────────────── */
  .topbar {{
    position: fixed; top: 0; left: 0; right: 0;
    z-index: 100; padding: 14px 16px;
    padding-top: max(14px, env(safe-area-inset-top));
    display: flex; justify-content: space-between; align-items: center;
    background: linear-gradient(180deg, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0.6) 70%, transparent 100%);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }}
  .brand {{
    display: flex; align-items: center; gap: 8px;
    font-weight: 800; font-size: 16px; letter-spacing: -0.02em;
  }}
  .brand-dot {{
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--brand);
    box-shadow: 0 0 0 4px rgba(255,90,31,0.18);
  }}
  .live-badge {{
    display: flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-radius: 999px;
    background: rgba(231,76,60,0.1); color: var(--red);
    font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
  }}
  .live-dot {{
    width: 7px; height: 7px; border-radius: 50%; background: var(--red);
    animation: livePulse 1.4s ease-in-out infinite;
  }}
  @keyframes livePulse {{
    0%, 100% {{ box-shadow: 0 0 0 0 rgba(231,76,60,0.6); }}
    50% {{ box-shadow: 0 0 0 6px rgba(231,76,60,0); }}
  }}

  /* ── Markers ──────────────────────────────────────────── */
  .marker-pin {{
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 6px 20px rgba(0,0,0,0.25); border: 3px solid #fff;
    border-radius: 50%; transform: translate(-50%, -50%);
  }}
  .marker-livreur {{
    width: 52px; height: 52px; font-size: 26px;
    background: var(--brand);
    animation: pulseRing 2.4s ease-out infinite;
  }}
  .marker-fixed {{
    width: 36px; height: 36px; font-size: 16px;
  }}
  .marker-partenaire {{ background: #111827; color: #fff; }}
  .marker-client {{ background: var(--green); color: #fff; }}
  @keyframes pulseRing {{
    0% {{ box-shadow: 0 0 0 0 rgba(255,90,31,0.55); }}
    70% {{ box-shadow: 0 0 0 22px rgba(255,90,31,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(255,90,31,0); }}
  }}

  /* ── Bottom sheet ─────────────────────────────────────── */
  .sheet {{
    position: fixed; left: 0; right: 0; bottom: 0;
    z-index: 50; background: var(--surface);
    border-radius: 24px 24px 0 0;
    box-shadow: var(--shadow);
    padding: 14px 20px;
    padding-bottom: max(20px, env(safe-area-inset-bottom));
    max-height: 75vh; overflow-y: auto;
    transform: translateY(0);
    transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .sheet-handle {{
    width: 40px; height: 4px; border-radius: 4px;
    background: #e5e7eb; margin: 0 auto 14px;
  }}

  /* ── ETA Hero ─────────────────────────────────────────── */
  .eta-hero {{
    display: flex; align-items: flex-start; gap: 14px;
    padding-bottom: 16px; border-bottom: 1px solid var(--line);
  }}
  .eta-icon {{
    flex-shrink: 0; width: 48px; height: 48px; border-radius: 14px;
    display: flex; align-items: center; justify-content: center;
    font-size: 24px;
    background: linear-gradient(135deg, #fff5ee 0%, #ffe4d3 100%);
  }}
  .eta-content {{ flex: 1; min-width: 0; }}
  .eta-label {{
    font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--brand); margin-bottom: 3px;
  }}
  .eta-value {{
    font-size: 22px; font-weight: 800; letter-spacing: -0.02em;
    line-height: 1.15;
  }}
  .eta-sub {{
    font-size: 13px; color: var(--ink-soft); margin-top: 2px;
  }}

  /* ── Stepper compact ──────────────────────────────────── */
  .stepper {{
    display: flex; align-items: center; gap: 6px;
    padding: 14px 0; border-bottom: 1px solid var(--line);
  }}
  .pip {{
    flex: 1; height: 4px; border-radius: 4px;
    background: #e9eaec; transition: background 0.6s;
  }}
  .pip.done {{ background: var(--green); }}
  .pip.active {{
    background: linear-gradient(90deg, var(--green) 0%, var(--brand) 100%);
    background-size: 200% 100%;
    animation: shimmer 2s linear infinite;
  }}
  .pip.cancelled {{ background: var(--red); }}
  @keyframes shimmer {{
    0% {{ background-position: 200% 0; }}
    100% {{ background-position: -200% 0; }}
  }}
  .step-label {{
    margin-top: 8px; font-size: 13px; font-weight: 600;
    color: var(--ink-soft); text-align: center;
  }}
  .step-label .icon {{ margin-right: 6px; }}

  /* ── Livreur card ─────────────────────────────────────── */
  .livreur-card {{
    display: none; align-items: center; gap: 14px;
    padding: 16px 0; border-bottom: 1px solid var(--line);
  }}
  .livreur-card.show {{ display: flex; }}
  .livreur-avatar {{
    flex-shrink: 0; width: 56px; height: 56px; border-radius: 50%;
    background: linear-gradient(135deg, #fff5ee 0%, #ffe4d3 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 26px; overflow: hidden;
    border: 2px solid var(--brand);
  }}
  .livreur-avatar img {{ width: 100%; height: 100%; object-fit: cover; }}
  .livreur-info {{ flex: 1; min-width: 0; }}
  .livreur-name {{
    font-size: 15px; font-weight: 700; letter-spacing: -0.01em;
  }}
  .livreur-meta {{
    font-size: 12px; color: var(--ink-soft); margin-top: 2px;
    display: flex; align-items: center; gap: 6px;
  }}
  .livreur-meta .dot {{
    width: 3px; height: 3px; border-radius: 50%; background: var(--ink-muted);
  }}
  .call-btn {{
    flex-shrink: 0; display: flex; align-items: center; gap: 6px;
    padding: 10px 16px; border-radius: 999px;
    background: var(--green); color: #fff;
    text-decoration: none; font-size: 13px; font-weight: 700;
    box-shadow: 0 4px 14px rgba(5,163,87,0.3);
    transition: transform 0.15s;
  }}
  .call-btn:active {{ transform: scale(0.96); }}

  /* ── Order info ───────────────────────────────────────── */
  .order-info {{
    padding-top: 14px;
    display: flex; justify-content: space-between; align-items: center;
    font-size: 12px; color: var(--ink-muted);
  }}
  .order-num {{ font-family: ui-monospace, 'SF Mono', Menlo, monospace; }}

  /* ── Banner annulé / livré ─────────────────────────────── */
  .final-banner {{
    display: none; padding: 18px 16px; border-radius: 14px;
    text-align: center; font-weight: 700;
    margin-bottom: 14px;
  }}
  .final-banner.cancelled {{
    display: block;
    background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
    color: #991b1b; border: 1px solid #fecaca;
  }}
  .final-banner.delivered {{
    display: block;
    background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%);
    color: #065f46; border: 1px solid #a7f3d0;
  }}
  .final-banner .big {{
    font-size: 28px; margin-bottom: 6px;
  }}
  .final-banner .label {{ font-size: 16px; }}
  .final-banner .sub {{ font-size: 12px; font-weight: 500; opacity: 0.8; margin-top: 4px; }}

  /* ── Loading initial ──────────────────────────────────── */
  .loading-overlay {{
    position: fixed; inset: 0; z-index: 200;
    display: flex; align-items: center; justify-content: center;
    background: var(--bg);
    transition: opacity 0.4s, visibility 0.4s;
  }}
  .loading-overlay.hidden {{ opacity: 0; visibility: hidden; }}
  .spinner {{
    width: 36px; height: 36px; border-radius: 50%;
    border: 3px solid #f3f4f6; border-top-color: var(--brand);
    animation: spin 0.8s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

  /* Responsive */
  @media (min-width: 720px) {{
    .sheet {{
      max-width: 480px; left: 50%; transform: translateX(-50%);
      border-radius: 24px;
      bottom: 24px; max-height: calc(100vh - 100px);
    }}
    .topbar {{ padding-left: 24px; padding-right: 24px; }}
  }}
</style>
</head>
<body>

<!-- Loading initial -->
<div class="loading-overlay" id="loadingOverlay">
  <div class="spinner"></div>
</div>

<!-- Carte plein écran -->
<div id="map"></div>

<!-- Placeholder quand on n'a pas encore de position -->
<div id="mapEmpty" style="display:none;">
  <div class="icon">📍</div>
  <h2>En attente d'un livreur</h2>
  <p>La position s'affichera ici dès qu'un livreur acceptera votre commande.</p>
</div>

<!-- Top bar -->
<div class="topbar">
  <div class="brand">
    <span class="brand-dot"></span>
    Sönaiyaa
  </div>
  <div class="live-badge">
    <span class="live-dot"></span>
    En direct
  </div>
</div>

<!-- Bottom sheet -->
<div class="sheet">
  <div class="sheet-handle"></div>

  <!-- Bannière finale (livré ou annulé) -->
  <div class="final-banner" id="finalBanner"></div>

  <!-- ETA Hero -->
  <div class="eta-hero" id="etaHero">
    <div class="eta-icon" id="etaIcon">🛵</div>
    <div class="eta-content">
      <div class="eta-label" id="etaLabel">Préparation</div>
      <div class="eta-value" id="etaValue">Votre livreur arrive bientôt</div>
      <div class="eta-sub" id="etaSub">Commande chez {partenaire}</div>
    </div>
  </div>

  <!-- Stepper compact -->
  <div>
    <div class="stepper">
      <div class="pip" id="pip-0"></div>
      <div class="pip" id="pip-1"></div>
      <div class="pip" id="pip-2"></div>
      <div class="pip" id="pip-3"></div>
    </div>
    <div class="step-label" id="stepLabel">
      <span class="icon">📋</span>
      <span id="stepLabelText">Commande reçue</span>
    </div>
  </div>

  <!-- Livreur card -->
  <div class="livreur-card" id="livreurCard">
    <div class="livreur-avatar" id="livreurAvatar">🛵</div>
    <div class="livreur-info">
      <div class="livreur-name" id="livreurNom"></div>
      <div class="livreur-meta" id="livreurMeta"></div>
    </div>
    <a class="call-btn" id="livreurTel" href="#">
      <span>📞</span> Appeler
    </a>
  </div>

  <!-- Order info -->
  <div class="order-info">
    <span>Commande</span>
    <span class="order-num">{numero}</span>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<script>
const TOKEN = '{token}';
const POLL_INTERVAL = 4000;
const MOTO_KMH = 25; // vitesse moyenne en ville

// État
let map = null;
let livreurMarker = null;
let partenaireMarker = null;
let clientMarker = null;
let routeLine = null;
let mapReady = false;
let livreurTargetPos = null;   // dernière position reçue
let livreurDisplayPos = null;  // position interpolée affichée
let animationFrameId = null;

const STATUS_CONFIG = {{
  CREEE:           {{ pip: 0, icon: '📋', label: 'Préparation',     title: 'Commande reçue',           desc: 'Le partenaire prépare votre commande' }},
  DIFFUSEE:        {{ pip: 0, icon: '📋', label: 'Préparation',     title: 'À la recherche d\\'un livreur', desc: 'Nous cherchons le livreur le plus proche' }},
  ACCEPTEE:        {{ pip: 1, icon: '🛵', label: 'Livreur en route', title: 'Livreur assigné',          desc: 'Se dirige vers le partenaire' }},
  EN_RECUPERATION: {{ pip: 1, icon: '🛵', label: 'Livreur en route', title: 'Livreur sur place',        desc: 'Récupération en cours' }},
  EN_LIVRAISON:    {{ pip: 2, icon: '🚀', label: 'En chemin',        title: 'En route vers vous',       desc: 'Le livreur arrive !' }},
  TERMINEE:        {{ pip: 3, icon: '🎉', label: 'Livré',            title: 'Livraison terminée',       desc: '' }},
}};

const VEHICULE_EMOJI = {{
  moto: '🛵', scooter: '🛵', velo: '🚲', voiture: '🚗', tricycle: '🛺',
}};

// ── Helpers géo ──
function haversineKm(a, b) {{
  const R = 6371;
  const toRad = (d) => d * Math.PI / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLng = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude), lat2 = toRad(b.latitude);
  const h = Math.sin(dLat/2)**2 + Math.cos(lat1)*Math.cos(lat2)*Math.sin(dLng/2)**2;
  return 2 * R * Math.asin(Math.sqrt(h));
}}
function formatDistance(km) {{
  if (km < 1) return Math.round(km * 1000) + ' m';
  return km.toFixed(1) + ' km';
}}
function formatEta(km) {{
  const min = Math.max(1, Math.round(km / MOTO_KMH * 60));
  if (min < 60) return min + ' min';
  const h = Math.floor(min / 60);
  return h + 'h' + (min % 60).toString().padStart(2, '0');
}}

// ── Map ──
function makeIcon(cls, content, size = 36) {{
  return L.divIcon({{
    className: '',
    html: '<div class="marker-pin ' + cls + '">' + content + '</div>',
    iconSize: [0, 0], // on positionne via CSS
  }});
}}

function ensureMap() {{
  if (mapReady) return;
  mapReady = true;
  document.getElementById('mapEmpty').style.display = 'none';
  map = L.map('map', {{
    zoomControl: false, attributionControl: true, scrollWheelZoom: false, tapTolerance: 30,
  }}).setView([9.535, -13.677], 13);
  L.control.zoom({{ position: 'topright' }}).addTo(map);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19, attribution: '© OpenStreetMap',
  }}).addTo(map);
}}

function smoothMoveLivreur() {{
  if (!livreurMarker || !livreurTargetPos || !livreurDisplayPos) return;
  const lerp = 0.18; // facteur d'interpolation par frame
  const dLat = livreurTargetPos.latitude - livreurDisplayPos.latitude;
  const dLng = livreurTargetPos.longitude - livreurDisplayPos.longitude;
  if (Math.abs(dLat) < 1e-7 && Math.abs(dLng) < 1e-7) {{
    livreurDisplayPos = {{ ...livreurTargetPos }};
  }} else {{
    livreurDisplayPos = {{
      latitude: livreurDisplayPos.latitude + dLat * lerp,
      longitude: livreurDisplayPos.longitude + dLng * lerp,
    }};
  }}
  livreurMarker.setLatLng([livreurDisplayPos.latitude, livreurDisplayPos.longitude]);
  animationFrameId = requestAnimationFrame(smoothMoveLivreur);
}}

function updateMap(data, vehiculeEmoji) {{
  const liv = data.livreur_position;
  const part = data.partenaire_position;
  const cli = data.client_position;

  if (!liv && !part && !cli) {{
    document.getElementById('mapEmpty').style.display = 'flex';
    return;
  }}

  ensureMap();

  if (part && !partenaireMarker) {{
    partenaireMarker = L.marker([part.latitude, part.longitude], {{
      icon: makeIcon('marker-fixed marker-partenaire', '🏪'),
    }}).addTo(map);
  }}

  if (cli && !clientMarker) {{
    clientMarker = L.marker([cli.latitude, cli.longitude], {{
      icon: makeIcon('marker-fixed marker-client', '🏠'),
    }}).addTo(map);
  }}

  if (liv) {{
    livreurTargetPos = liv;
    if (!livreurMarker) {{
      livreurDisplayPos = {{ ...liv }};
      livreurMarker = L.marker([liv.latitude, liv.longitude], {{
        icon: makeIcon('marker-livreur', vehiculeEmoji || '🛵'),
        zIndexOffset: 1000,
      }}).addTo(map);
    }}
    if (animationFrameId) cancelAnimationFrame(animationFrameId);
    animationFrameId = requestAnimationFrame(smoothMoveLivreur);
  }}

  // Ligne reliant le livreur à sa destination courante (partenaire ou client)
  const dest = (data.status === 'EN_LIVRAISON' || data.status === 'TERMINEE') ? cli : part;
  if (liv && dest) {{
    const coords = [[liv.latitude, liv.longitude], [dest.latitude, dest.longitude]];
    if (!routeLine) {{
      routeLine = L.polyline(coords, {{
        color: '#FF5A1F', weight: 4, opacity: 0.6, dashArray: '8 10', lineCap: 'round',
      }}).addTo(map);
    }} else {{
      routeLine.setLatLngs(coords);
    }}
  }}

  // Auto-fit
  const points = [];
  if (liv) points.push([liv.latitude, liv.longitude]);
  if (dest) points.push([dest.latitude, dest.longitude]);
  if (points.length === 1) {{
    map.setView(points[0], 15);
  }} else if (points.length > 1) {{
    map.fitBounds(points, {{ padding: [80, 80], maxZoom: 16 }});
  }}
}}

// ── ETA & UI ──
function computeEta(data) {{
  const liv = data.livreur_position;
  const dest = (data.status === 'EN_LIVRAISON') ? data.client_position : data.partenaire_position;
  if (!liv || !dest) return null;
  const km = haversineKm(liv, dest);
  return {{ km, eta: formatEta(km), distance: formatDistance(km) }};
}}

function updateEtaHero(data, vehiculeEmoji) {{
  const cfg = STATUS_CONFIG[data.status];
  if (!cfg) return;

  document.getElementById('etaIcon').textContent = vehiculeEmoji || cfg.icon;
  document.getElementById('etaLabel').textContent = cfg.label;

  if (data.status === 'EN_LIVRAISON' || data.status === 'ACCEPTEE' || data.status === 'EN_RECUPERATION') {{
    const e = computeEta(data);
    if (e) {{
      const dest = (data.status === 'EN_LIVRAISON') ? 'chez vous' : 'chez le partenaire';
      document.getElementById('etaValue').textContent = 'Arrivée dans ~' + e.eta;
      document.getElementById('etaSub').textContent = 'Le livreur est à ' + e.distance + ' — ' + dest;
      return;
    }}
  }}
  document.getElementById('etaValue').textContent = cfg.title;
  document.getElementById('etaSub').textContent = cfg.desc || ('Commande chez ' + {partenaire_js});
}}

function updateStepper(data) {{
  const cfg = STATUS_CONFIG[data.status];
  const idx = cfg ? cfg.pip : 0;
  for (let i = 0; i < 4; i++) {{
    const el = document.getElementById('pip-' + i);
    el.className = 'pip';
    if (data.status === 'ANNULEE') {{ if (i === 0) el.classList.add('cancelled'); }}
    else if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add(data.status === 'TERMINEE' ? 'done' : 'active');
  }}
  if (cfg) {{
    document.querySelector('.step-label .icon').textContent = cfg.icon;
    document.getElementById('stepLabelText').textContent = cfg.title;
  }}
}}

function updateLivreurCard(data, vehiculeEmoji) {{
  const card = document.getElementById('livreurCard');
  if (!data.livreur_nom) {{ card.classList.remove('show'); return; }}
  card.classList.add('show');

  const avatar = document.getElementById('livreurAvatar');
  if (data.livreur_photo) {{
    avatar.innerHTML = '<img src="' + data.livreur_photo + '" alt="">';
  }} else {{
    avatar.textContent = vehiculeEmoji || '🛵';
  }}

  document.getElementById('livreurNom').textContent = data.livreur_nom;

  const meta = document.getElementById('livreurMeta');
  const parts = [];
  if (data.livreur_vehicule) parts.push(data.livreur_vehicule.charAt(0).toUpperCase() + data.livreur_vehicule.slice(1));
  if (data.livreur_note && data.livreur_note > 0) parts.push('★ ' + data.livreur_note.toFixed(1));
  meta.innerHTML = parts.join('<span class="dot"></span>');

  const tel = document.getElementById('livreurTel');
  if (data.livreur_telephone) {{
    tel.href = 'tel:' + data.livreur_telephone;
    tel.style.display = 'flex';
  }} else {{
    tel.style.display = 'none';
  }}
}}

function updateFinalBanner(data) {{
  const b = document.getElementById('finalBanner');
  if (data.status === 'ANNULEE') {{
    b.className = 'final-banner cancelled';
    b.innerHTML = '<div class="big">❌</div><div class="label">Commande annulée</div><div class="sub">Contactez le partenaire pour plus d\\'informations</div>';
    document.getElementById('etaHero').style.display = 'none';
  }} else if (data.status === 'TERMINEE') {{
    b.className = 'final-banner delivered';
    b.innerHTML = '<div class="big">🎉</div><div class="label">Livré avec succès</div><div class="sub">Merci d\\'avoir utilisé Sönaiyaa</div>';
    document.getElementById('etaHero').style.display = 'none';
  }} else {{
    b.className = 'final-banner';
    document.getElementById('etaHero').style.display = 'flex';
  }}
}}

function updateUI(data) {{
  const vehiculeEmoji = data.livreur_vehicule ? (VEHICULE_EMOJI[data.livreur_vehicule.toLowerCase()] || '🛵') : '🛵';
  updateEtaHero(data, vehiculeEmoji);
  updateStepper(data);
  updateLivreurCard(data, vehiculeEmoji);
  updateFinalBanner(data);
  updateMap(data, vehiculeEmoji);
}}

// ── Polling ──
let firstLoad = true;
function poll() {{
  fetch('/suivi/' + TOKEN + '/status')
    .then(r => r.json())
    .then(data => {{
      updateUI(data);
      if (firstLoad) {{
        firstLoad = false;
        document.getElementById('loadingOverlay').classList.add('hidden');
      }}
      if (data.status !== 'TERMINEE' && data.status !== 'ANNULEE') {{
        setTimeout(poll, POLL_INTERVAL);
      }}
    }})
    .catch(() => setTimeout(poll, 8000));
}}

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
