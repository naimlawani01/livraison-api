# 📦 Backend - Plateforme de Livraison

Backend FastAPI pour la plateforme de livraison connectant partenaires (commerces) et livreurs (taxis-motos).

## 🚀 Installation

### Prérequis

- Python 3.9+
- PostgreSQL 13+
- Redis (optionnel pour cache)

### Configuration

1. Créer un environnement virtuel:

```bash
python -m venv venv
source venv/bin/activate  # Sur Linux/Mac
# ou
venv\Scripts\activate  # Sur Windows
```

2. Installer les dépendances:

```bash
pip install -r requirements.txt
```

3. Configurer les variables d'environnement:

```bash
cp .env.example .env
# Éditer le fichier .env avec vos configurations
```

4. Créer la base de données PostgreSQL:

```sql
CREATE DATABASE livraison_db;
```

5. Lancer les migrations (Alembic):

```bash
alembic upgrade head
```

## 🏃 Lancement

### Mode développement

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Mode production

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 📚 Documentation API

Une fois l'application lancée, accéder à:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 🏗️ Architecture

```
backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/      # Routes API
│   │       │   ├── auth.py
│   │       │   ├── partenaires.py
│   │       │   ├── livreurs.py
│   │       │   ├── commandes.py
│   │       │   ├── tracking.py
│   │       │   └── admin.py
│   │       └── api.py          # Router principal
│   ├── core/
│   │   ├── config.py           # Configuration
│   │   ├── security.py         # JWT, hashing
│   │   └── database.py         # Connexion DB
│   ├── models/                 # Modèles SQLAlchemy
│   │   ├── user.py
│   │   ├── partenaire.py
│   │   ├── livreur.py
│   │   └── commande.py
│   ├── schemas/                # Schémas Pydantic
│   │   ├── user.py
│   │   ├── partenaire.py
│   │   ├── livreur.py
│   │   └── commande.py
│   ├── services/               # Logique métier
│   │   ├── geolocation_service.py
│   │   ├── matching_service.py
│   │   ├── notification_service.py
│   │   └── sms_service.py
│   ├── utils/
│   │   └── dependencies.py     # Dépendances FastAPI
│   └── main.py                 # Point d'entrée
├── requirements.txt
├── .env.example
└── README.md
```

## 🔑 Endpoints Principaux

### Authentication

- `POST /api/v1/auth/register` - Inscription
- `POST /api/v1/auth/request-otp` - Demander un code OTP
- `POST /api/v1/auth/verify-otp` - Vérifier OTP et connexion
- `POST /api/v1/auth/login` - Connexion classique

### Partenaires

- `POST /api/v1/partenaires/` - Créer profil partenaire
- `GET /api/v1/partenaires/me` - Mon profil
- `PATCH /api/v1/partenaires/me` - Mettre à jour profil

### Livreurs

- `POST /api/v1/livreurs/` - Créer profil livreur
- `GET /api/v1/livreurs/me` - Mon profil
- `PATCH /api/v1/livreurs/me/location` - MAJ position GPS
- `PATCH /api/v1/livreurs/me/disponibilite` - Changer disponibilité

### Commandes

- `POST /api/v1/commandes/` - Créer commande (partenaire)
- `GET /api/v1/commandes/me` - Mes commandes (partenaire)
- `GET /api/v1/commandes/livreur/disponibles` - Courses disponibles (livreur)
- `POST /api/v1/commandes/{id}/accepter` - Accepter course (livreur)
- `PATCH /api/v1/commandes/{id}/statut` - Changer statut (livreur)
- `POST /api/v1/commandes/{id}/evaluer` - Évaluer livreur (partenaire)

### Admin

- `GET /api/v1/admin/stats` - Statistiques plateforme
- `GET /api/v1/admin/livreurs/en-attente` - Livreurs à valider
- `POST /api/v1/admin/livreurs/{id}/valider` - Valider livreur
- `POST /api/v1/admin/livreurs/{id}/suspendre` - Suspendre livreur

## 🔌 WebSocket

Endpoint temps réel: `ws://localhost:8000/ws/{user_id}/{user_type}`

Types côté WebSocket: `partenaire`, `livreur`, `admin`

### Messages WebSocket

```json
// Ping
{
  "type": "ping"
}

// Mise à jour position (livreur)
{
  "type": "location_update",
  "latitude": 6.1234,
  "longitude": 1.2345
}

// Nouvelle commande (serveur -> livreurs)
{
  "type": "nouvelle_commande",
  "data": { ... }
}
```

## 🔐 Sécurité

- JWT pour l'authentification
- Hashing bcrypt pour les mots de passe
- OTP par SMS pour vérification
- CORS configuré
- Validation Pydantic

## 🧪 Tests

```bash
pytest
```

## 📝 Configuration Firebase (Notifications)

1. Créer un projet Firebase
2. Télécharger le fichier de credentials JSON
3. Définir `FIREBASE_CREDENTIALS_PATH` dans `.env`

## 📝 Configuration Twilio (SMS/OTP)

1. Créer un compte Twilio
2. Obtenir Account SID, Auth Token et numéro de téléphone
3. Configurer dans `.env`

## 🚢 Déploiement

### Docker (recommandé)

```bash
docker build -t livraison-backend .
docker run -p 8000:8000 livraison-backend
```

### Serveurs compatibles

- Heroku
- DigitalOcean App Platform
- AWS EC2/ECS
- Google Cloud Run
- VPS avec Nginx + Gunicorn

## 📄 License

Propriétaire
