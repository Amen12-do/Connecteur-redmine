"""
Connecteur Redmine avec e-mail en Python Flask
---------------------------------------------
Ce script permet de:
1. Recevoir des e-mails et créer des tickets Redmine correspondants
2. Envoyer des notifications par e-mail lors de mises à jour de tickets Redmine
3. Synchroniser les commentaires entre e-mails et tickets Redmine
"""

from flask import Flask, request, jsonify
import os
import smtplib
from email.message import EmailMessage
from email.parser import Parser
import email
import imaplib
import time
import logging
from redminelib import Redmine
from redminelib.exceptions import ResourceNotFoundError
import yaml # type: ignore
import schedule # type: ignore
import threading

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("redmine_connector.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialisation de l'application Flask
app = Flask(__name__)

# Chargement de la configuration
def load_config():
    with open('config.yaml', 'r') as file:
        return yaml.safe_load(file)

config = load_config()

# Configuration Redmine
redmine = Redmine(
    config['redmine']['url'],
    key=config['redmine']['api_key'],
    version=config['redmine']['api_version']
)

# Configuration e-mail
EMAIL_IMAP_SERVER = config['email']['imap_server']
EMAIL_SMTP_SERVER = config['email']['smtp_server']
EMAIL_PORT = config['email']['port']
EMAIL_USER = config['email']['username']
EMAIL_PASSWORD = config['email']['password']
EMAIL_CHECK_INTERVAL = config['email']['check_interval']

# -------------------- FONCTIONS POUR REDMINE --------------------

def create_redmine_issue(subject, description, project_id, tracker_id=None, status_id=None, priority_id=None, assigned_to_id=None):
    """Crée un ticket dans Redmine"""
    try:
        issue_data = {
            'project_id': project_id,
            'subject': subject,
            'description': description,
        }
        
        if tracker_id:
            issue_data['tracker_id'] = tracker_id
        if status_id:
            issue_data['status_id'] = status_id
        if priority_id:
            issue_data['priority_id'] = priority_id
        if assigned_to_id:
            issue_data['assigned_to_id'] = assigned_to_id
            
        issue = redmine.issue.create(**issue_data)
        logger.info(f"Ticket Redmine créé avec succès: #{issue.id}")
        return issue
    except Exception as e:
        logger.error(f"Erreur lors de la création du ticket Redmine: {e}")
        return None

def update_redmine_issue(issue_id, **kwargs):
    """Met à jour un ticket dans Redmine"""
    try:
        issue = redmine.issue.get(issue_id)
        redmine.issue.update(issue_id, **kwargs)
        logger.info(f"Ticket Redmine #{issue_id} mis à jour avec succès")
        return True
    except ResourceNotFoundError:
        logger.error(f"Ticket Redmine #{issue_id} non trouvé")
        return False
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour du ticket Redmine #{issue_id}: {e}")
        return False

def add_comment_to_redmine_issue(issue_id, comment):
    """Ajoute un commentaire à un ticket Redmine"""
    return update_redmine_issue(issue_id, notes=comment)

def get_redmine_issue(issue_id):
    """Récupère les détails d'un ticket Redmine"""
    try:
        return redmine.issue.get(issue_id, include=['journals', 'attachments'])
    except ResourceNotFoundError:
        logger.error(f"Ticket Redmine #{issue_id} non trouvé")
        return None
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du ticket Redmine #{issue_id}: {e}")
        return None

# -------------------- FONCTIONS POUR L'E-MAIL --------------------

def send_email(to_email, subject, body, cc=None, attachments=None):
    """Envoie un e-mail"""
    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = EMAIL_USER
        msg['To'] = to_email
        if cc:
            msg['Cc'] = cc
        msg.set_content(body)
        
        # Ajout des pièces jointes
        if attachments:
            for attachment in attachments:
                with open(attachment, 'rb') as f:
                    file_data = f.read()
                    file_name = os.path.basename(attachment)
                msg.add_attachment(file_data, maintype='application', subtype='octet-stream', filename=file_name)
        
        # Envoi de l'e-mail
        with smtplib.SMTP(EMAIL_SERVER, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"E-mail envoyé avec succès à {to_email}")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'e-mail: {e}")
        return False

def check_emails():
    """Vérifie les nouveaux e-mails et crée des tickets Redmine correspondants"""
    try:
        mail = imaplib.IMAP4_SSL(EMAIL_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select('inbox')
        
        # Recherche des e-mails non lus
        status, data = mail.search(None, 'UNSEEN')
        if status != 'OK':
            logger.error("Impossible de rechercher les e-mails")
            return
        
        for num in data[0].split():
            status, data = mail.fetch(num, '(RFC822)')
            if status != 'OK':
                logger.error(f"Impossible de récupérer l'e-mail {num}")
                continue
            
            raw_email = data[0][1]
            email_message = email.message_from_bytes(raw_email)
            
            subject = email_message['Subject']
            from_email = email.utils.parseaddr(email_message['From'])[1]
            
            # Extraction du corps de l'e-mail
            body = ""
            if email_message.is_multipart():
                for part in email_message.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    
                    if "attachment" not in content_disposition:
                        if content_type == "text/plain":
                            body = part.get_payload(decode=True).decode()
                            break
            else:
                body = email_message.get_payload(decode=True).decode()
            
            # Création du ticket Redmine
            description = f"De: {from_email}\n\n{body}"
            project_id = config['redmine']['default_project_id']
            issue = create_redmine_issue(subject, description, project_id)
            
            if issue:
                # Marquer l'e-mail comme lu
                mail.store(num, '+FLAGS', '\\Seen')
                
                # Envoyer une confirmation par e-mail
                confirmation_subject = f"[Redmine #{issue.id}] Votre demande a été enregistrée"
                confirmation_body = f"""
Bonjour,

Votre demande a été enregistrée dans notre système avec le numéro de ticket #{issue.id}.
Vous pouvez suivre l'évolution de votre demande en répondant à cet e-mail.

Cordialement,
L'équipe support
                """
                send_email(from_email, confirmation_subject, confirmation_body)
        
        mail.close()
        mail.logout()
    except Exception as e:
        logger.error(f"Erreur lors de la vérification des e-mails: {e}")

def process_redmine_updates():
    """Traite les mises à jour de tickets Redmine et envoie des notifications par e-mail"""
    try:
        # Récupération des tickets mis à jour récemment
        updated_since = int(time.time()) - (EMAIL_CHECK_INTERVAL * 60)
        issues = redmine.issue.filter(updated_on=f">=2000-01-01T00:00:00Z", sort="updated_on:desc", limit=100)
        
        for issue in issues:
            # Vérification si le ticket a été mis à jour récemment
            issue_detail = get_redmine_issue(issue.id)
            if not issue_detail:
                continue
            
            # Récupération de l'adresse e-mail du demandeur (à partir de la description)
            requester_email = None
            description_lines = issue_detail.description.split('\n')
            for line in description_lines:
                if line.startswith("De:"):
                    requester_email = line.replace("De:", "").strip()
                    break
            
            if not requester_email:
                continue
            
            # Vérification des nouvelles notes (commentaires)
            for journal in issue_detail.journals:
                if hasattr(journal, 'created_on') and journal.created_on >= updated_since:
                    if hasattr(journal, 'notes') and journal.notes:
                        # Envoi d'une notification par e-mail
                        subject = f"[Redmine #{issue.id}] Mise à jour: {issue.subject}"
                        body = f"""
Bonjour,

Une mise à jour a été effectuée sur votre ticket #{issue.id}:

{journal.notes}

Vous pouvez répondre à cet e-mail pour ajouter un commentaire au ticket.

Cordialement,
L'équipe support
                        """
                        send_email(requester_email, subject, body)
    except Exception as e:
        logger.error(f"Erreur lors du traitement des mises à jour Redmine: {e}")

# -------------------- ROUTES FLASK --------------------

@app.route('/webhook/redmine', methods=['POST'])
def redmine_webhook():
    """Webhook pour recevoir les notifications de Redmine"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Données JSON invalides"}), 400
        
        # Traitement de l'événement Redmine
        if 'issue' in data:
            issue_id = data['issue']['id']
            issue = get_redmine_issue(issue_id)
            
            if issue:
                # Recherche de l'adresse e-mail du demandeur
                requester_email = None
                description_lines = issue.description.split('\n')
                for line in description_lines:
                    if line.startswith("De:"):
                        requester_email = line.replace("De:", "").strip()
                        break
                
                if requester_email:
                    # Envoi d'une notification par e-mail
                    subject = f"[Redmine #{issue.id}] Mise à jour: {issue.subject}"
                    body = f"""
Bonjour,

Une mise à jour a été effectuée sur votre ticket #{issue.id}.

Vous pouvez répondre à cet e-mail pour ajouter un commentaire au ticket.

Cordialement,
L'équipe support
                    """
                    send_email(requester_email, subject, body)
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Erreur dans le webhook Redmine: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/email', methods=['POST'])
def email_webhook():
    """Webhook pour recevoir les e-mails"""
    try:
        data = request.json
        if not data or 'from' not in data or 'subject' not in data or 'body' not in data:
            return jsonify({"error": "Données JSON invalides"}), 400
        
        from_email = data['from']
        subject = data['subject']
        body = data['body']
        
        # Vérification si l'e-mail est une réponse à un ticket existant
        import re
        ticket_id_match = re.search(r'\[Redmine #(\d+)\]', subject)
        
        if ticket_id_match:
            # Réponse à un ticket existant
            ticket_id = ticket_id_match.group(1)
            add_comment_to_redmine_issue(ticket_id, f"Commentaire par e-mail de {from_email}:\n\n{body}")
        else:
            # Création d'un nouveau ticket
            description = f"De: {from_email}\n\n{body}"
            project_id = config['redmine']['default_project_id']
            issue = create_redmine_issue(subject, description, project_id)
            
            if issue:
                # Envoyer une confirmation par e-mail
                confirmation_subject = f"[Redmine #{issue.id}] Votre demande a été enregistrée"
                confirmation_body = f"""
Bonjour,

Votre demande a été enregistrée dans notre système avec le numéro de ticket #{issue.id}.
Vous pouvez suivre l'évolution de votre demande en répondant à cet e-mail.

Cordialement,
L'équipe support
                """
                send_email(from_email, confirmation_subject, confirmation_body)
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Erreur dans le webhook e-mail: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- TÂCHES PLANIFIÉES --------------------

def run_schedule():
    """Exécute les tâches planifiées"""
    schedule.every(EMAIL_CHECK_INTERVAL).minutes.do(check_emails)
    schedule.every(EMAIL_CHECK_INTERVAL).minutes.do(process_redmine_updates)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

# -------------------- DÉMARRAGE DE L'APPLICATION --------------------

if __name__ == '__main__':
    # Démarrage des tâches planifiées dans un thread séparé
    scheduler_thread = threading.Thread(target=run_schedule)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    # Démarrage du serveur Flask
    app.run(host='0.0.0.0', port=5000, debug=False)