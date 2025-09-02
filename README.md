# IPP : Imt Planning Proxy

IPP est un serveur "proxy" qui permet de récupérer des fichiers ICS (iCalendar) à partir de `webdbf`(site de planning de l'Institut Mines-Télécom d'Alès). Il permet de créer un lien direct et sans authentification vers votre planning, contournant ainsi les restrications d'accès et permettant l'import simple dans des applications comme Google Calendar ou Outlook.

## Paramétrage

Renommer le fichier config.ini.example en config.ini et remplir les informations d'identification nécessaires.

`LOGIN_URL` correspond à la page de connexion de `webdbf`. Vous n'avez pas besoin de changer ce paramètre.

`URL` correspond à l'URL du fichier ICS que vous souhaitez récupérer. Il est différent pour chaque calendrier. Pour le récupérez, sur `webdbf`, accédez à votre emploi du temps et cliquez sur "syncrho". Récupérz le contenu du champ "url". 
![alt text](image.png)

`USERNAME` et `PASSWORD` correspondent à vos identifiants de connexion pour `webdbf`. Vous devez les remplir avec vos propres informations d'identification. Ce sont normalement les mêmes que ceux pour le mail.

Vous pouvez créer autant de section que vous le souhaitez dans le fichier `config.ini`. Chaque section aura une route correspondante dans l'application.

## Déploiement

La méthode de déploiement recommandée pour IPP est docker et un reverse proxy. Lancez le projet avec `docker-compose up -d` et faites pointer votre reverse proxy vers le port 5000 du conteneur.
