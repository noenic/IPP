# IPP — IMT Planning Proxy

Petit proxy maison pour récupérer des fichiers ICS depuis webdbf (le site de planning de l'IMT d'Alès). Car il n'est pas possible de s'abonner directement aux ICS en dehors du réseau EMA.
C'est un fork "à la va-vite" du projet de [BaptisteP31](https://github.com/BaptisteP31/IPP), avec quelques ajouts personnels :
- Pas de requêtes en temps réel : on scrape toutes les ~5 minutes et on sert la dernière version disponible.
- Horodatage des événements : on ajoute quand l'ICS a été récupéré et importé, pour savoir si c'est frais ou périmé.
- Simple, léger, et fait pour être déployé en quelques minutes.

---

## Fonctionnalités
- Mise en cache périodique des ICS pour éviter de crasher webdbf.
- Endpoint public qui renvoie l'ICS prêt à être importé (Google Calendar, Outlook...).
- Génération de tokens étudiants (par l'admin) pour contrôler l'accès.

---

## Sécurité (l'essentiel, mais sérieusement quand même)
- Si vous exposez ça sur Internet : générez un token unique par étudiant (Comme ça, si un token fuit, vous pouvez le révoquer sans impacter les autres et taper sur l'utilisateur fautif).
- Les tokens étudiants sont stockés en clair dans un JSON local (j'allais pas faire une DB pour ça quand même).

### Créer un token (API)
POST /admin/create_token  
Headers :
```http
Authorization: Bearer <MASTER_TOKEN>
Content-Type: application/json
```

Body (JSON) :
```json
{
    "name": "nom_etudiant"
}
```

Réponse (JSON) :
```json
{
    "name": "nom_etudiant",
    "token": "nom_etudiant_<token_genere>"
}
```

Exemple curl :
```bash
curl -X POST http://votre-ipp.fr/admin/create_token \
    -H "Authorization: Bearer votre_admin_static_token" \
    -H "Content-Type: application/json" \
    -d '{"name":"jean.dupont"}'
```

Pour obtenir l'ICS d'une section :
```
GET /2A-SR?token=<token_genere>
```

---

## Configuration (.env)
Copiez `.env.example` → `.env` et remplissez :
- LOGIN_URL : URL de connexion webdbf (ne pas toucher sauf si IMT change).
- WEB_USERNAME / WEB_PASSWORD : vos identifiants webdbf (nom.prenom).
- ADMIN_STATIC_TOKEN : token admin (gardez-le secret).
- SECTIONS : `NomSection:suffixe_url_ics,...` (ex: `2A-SR:eleve/12345,2A-DL:eleve/67890`)
- FLASK_HOST / FLASK_PORT : par défaut `0.0.0.0:5000`.

Pour récupérer le suffixe ICS : sur webdbf → Syncro → copier le champ "url".
Par pitié, protégez bien ce fichier `.env` (ne le commitez pas et configurez les permissions, ou alors utilisez des variables d'environnement système).

Pour les sections, reférez-vous à ce screenshot (ici , c'est promo/75) :

![screenshot](./image.png)

---

## Déploiement (rapide)
Recommandé : Docker + reverse proxy (Nginx / Traefik).

Build :
```bash
docker build -t ipp .
```
Lancer :
```bash
docker run -d --name ipp -p 5000:5000 --env-file .env ipp
```
ou avec docker-compose :
```bash
docker-compose up -d
```
Configurez votre reverse proxy vers le port 5000.

---

## Dépendances
Listées dans requirements.txt :
- Flask==3.0.0
- requests==2.31.0
- python-dotenv==1.0.0

Installer :
```bash
pip install -r requirements.txt
```

---

## Licence & contributions
Licence MIT. Contributions bienvenues — issues et PR acceptées (je regarde quand j'ai le temps).

---

## Avertissements rapides
- Ne partagez pas vos identifiants ni le token admin.
- Respectez l'IMT : intervalle de scrap par défaut = 5 minutes pour ne pas les flooder.
- Stockage des tokens en clair : pratique mais pas hyper sécurisé — améliorez si vous voulez.

Je le répète : Faites attention avec vos identifiants webdbf, vous êtes responsables de leur sécurité, et s'ils fuient, c'est un enfer de les changer.