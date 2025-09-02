from flask import Flask, Response
import requests
from configparser import ConfigParser

def load_config(config_file='config.ini'):
    """Load and return the configuration."""
    config = ConfigParser()
    config.read(config_file)
    return config

def create_app(config):
    """Create and configure the Flask app with routes for each config section."""
    app = Flask(__name__)

    for section in config.sections():
        # Create a route with a default argument to bind the current section.
        app.add_url_rule(
            f'/{section}',
            endpoint=section,
            view_func=lambda s=section: get_ics(config, s)
        )

    return app

def get_ics(config, section):
    """Perform authentication and retrieve the ICS file for the given section."""
    login_url = config[section]['LOGIN_URL']
    target_url = config[section]['URL']

    form_data = {
        'Username': config[section]['USERNAME'],
        'Password': config[section]['PASSWORD'],
        'url': '',
        'login': ''
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://webdfd.mines-ales.fr',
        'Referer': 'https://webdfd.mines-ales.fr/planning-eleves/index.php',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i'
    }

    session = requests.Session()

    try:
        login_response = session.post(login_url, headers=headers, data=form_data)
        login_response.raise_for_status()

        ics_response = session.get(target_url, headers=headers)
        ics_response.raise_for_status()

        return Response(
            ics_response.content,
            mimetype='text/calendar',
            headers={
                'Content-Disposition': 'attachment; filename=planning.ics'
            }
        )
    except requests.exceptions.RequestException as e:
        return f"Error fetching ICS file: {e}", 500

if __name__ == '__main__':
    config = load_config()
    print("Loaded configuration sections:", config.sections())
    app = create_app(config)
    app.run(host='0.0.0.0', port=5000)

# Production
config = load_config('config.ini')
print("Loaded configuration sections:", config.sections())
app = create_app(config)


# Default page lists available sections
@app.route('/')
def index():
    config = load_config()
    return f"Available sections : {', '.join(config.sections())}"