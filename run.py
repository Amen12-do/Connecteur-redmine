from waitress import serve
from connect import app  # Importez votre application Flask

if __name__ == '__main__':
    print("Démarrage du serveur Waitress sur http://0.0.0.0:5000")
    serve(app, host='0.0.0.0', port=5000)