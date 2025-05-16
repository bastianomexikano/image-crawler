# app.py
from flask import Flask, request, render_template, jsonify
import os
import boto3 # AWS SDK für Python
from botocore.exceptions import ClientError # Für Boto3 Fehlerbehandlung
import logging
import json # Für das Erstellen der SQS Nachricht

# Importiere Funktionen und Konfigurationen aus crawler.py
# Stelle sicher, dass crawler.py im PYTHONPATH ist oder im selben Verzeichnis liegt.
from crawler import search_media_by_hashtag, process_media, getCreds, crawled_media_table # crawled_media_table importieren
from boto3.dynamodb.conditions import Key, Attr # Für DynamoDB FilterExpressions

app = Flask(
    __name__,
    template_folder=os.path.join("..", "frontend", "templates"),
    static_folder=os.path.join("..", "frontend", "static")
)

# Konfigurationen laden
creds = getCreds()
ACCESS_TOKEN = creds.get('access_token')
INSTAGRAM_BUSINESS_ACCOUNT_ID = creds.get('instagram_business_id')
S3_BUCKET_NAME = creds.get('s3_bucket_name')
S3_REGION = creds.get('s3_bucket_region', creds.get('dynamodb_region')) # Fallback für S3 Region
SQS_QUEUE_URL = creds.get('sqs_queue_url')
SQS_REGION = creds.get('sqs_queue_region', S3_REGION) # Fallback für SQS Region

# Überprüfung der Konfiguration (vereinfacht, da bereits im Original vorhanden)
# ... (Ihre bestehenden Konfigurationsprüfungen) ...

# S3 und SQS Clients initialisieren
s3_client_app = None
sqs_client_app = None
try:
    if S3_BUCKET_NAME and S3_REGION:
        s3_client_app = boto3.client('s3', region_name=S3_REGION)
    else:
        print("WARNUNG: S3 Client nicht initialisiert, da S3_BUCKET_NAME oder S3_REGION fehlen.")

    if SQS_QUEUE_URL and SQS_REGION:
        sqs_client_app = boto3.client('sqs', region_name=SQS_REGION)
    else:
        print("WARNUNG: SQS Client nicht initialisiert, da SQS_QUEUE_URL oder SQS_REGION fehlen.")
except Exception as e:
    print(f"Schwerwiegender Fehler bei der Initialisierung von AWS Clients in app.py: {e}")


@app.route("/", methods=["GET", "POST"])
def index():
    images_data_from_direct_crawler = [] # Umbenannt für Klarheit
    processed_template_images = []
    search_term_display = ""
    # current_hashtag wird direkt im Template gesetzt, wenn es als Parameter übergeben wird
    message_to_user = ""
    current_hashtag_query_for_template = "" # Für das value-Attribut im Input-Feld

    if request.method == "POST":
        hashtag_query = request.form.get("hashtag", "").strip()
        current_hashtag_query_for_template = hashtag_query # Für das Input-Feld nach dem POST

        if hashtag_query:
            search_term_display = f"Ergebnisse für Hashtag: #{hashtag_query.lstrip('#')}"
            app.logger.info(f"Starte Hashtag-Suche (direkt und via SQS) für: {hashtag_query}")

            # 1. Nachricht an SQS senden
            if sqs_client_app and SQS_QUEUE_URL:
                try:
                    message_body = {"hashtag": hashtag_query.lstrip('#'), "platform": "instagram"} # Hashtag ohne # senden
                    response = sqs_client_app.send_message(
                        QueueUrl=SQS_QUEUE_URL,
                        MessageBody=json.dumps(message_body)
                    )
                    message_id = response.get('MessageId')
                    app.logger.info(f"Nachricht für Hashtag '{hashtag_query}' an SQS gesendet. Message ID: {message_id}")
                    message_to_user = f"Suchauftrag für '{hashtag_query}' an Hintergrundverarbeitung gesendet. Ergebnisse unten sind von der direkten Suche und werden ggf. erweitert."
                except ClientError as e:
                    app.logger.error(f"ClientError beim Senden der Nachricht an SQS für Hashtag '{hashtag_query}': {e}")
                    if "NonExistentQueue" in str(e):
                        message_to_user = "Fehler: Die angegebene SQS-Warteschlange existiert nicht oder die Region ist falsch. Bitte Konfiguration prüfen."
                    else:
                        message_to_user = "Fehler beim Senden des Suchauftrags an die Hintergrundverarbeitung."
                except Exception as e_general:
                    app.logger.error(f"Allgemeiner Fehler beim Senden der SQS Nachricht für '{hashtag_query}': {e_general}")
                    message_to_user = "Unerwarteter Fehler beim Senden des Suchauftrags."
            else:
                app.logger.warning("SQS Client oder Queue URL nicht konfiguriert.")
                # ... (Ihre bestehende Logik für message_to_user bei SQS-Fehlern) ...


            # 2. Crawler direkt aufrufen für sofortige (blockierende) Anzeige
            # Dieser Teil liefert eine erste schnelle Ansicht. Die JS-Logik holt dann alle Daten.
            app.logger.info(f"Starte direkten Crawler-Aufruf für Hashtag: {hashtag_query}")
            try:
                if not ACCESS_TOKEN:
                    app.logger.error("Instagram ACCESS_TOKEN ist nicht konfiguriert!")
                    raise ValueError("Instagram Access Token fehlt.")

                images_data_from_direct_crawler = search_media_by_hashtag(
                    ACCESS_TOKEN,
                    INSTAGRAM_BUSINESS_ACCOUNT_ID,
                    hashtag_query.lstrip('#') # Hashtag ohne # an Crawler übergeben
                )
                app.logger.info(f"Direkter Crawler lieferte {len(images_data_from_direct_crawler)} Elemente für Hashtag '{hashtag_query}'.")
                # ... (Ihre bestehende Logik für message_to_user bei direkter Suche) ...

            except ValueError as e_val:
                app.logger.error(f"Fehler beim direkten Crawler-Aufruf für '{hashtag_query}': {e_val}")
                # ... (Ihre bestehende Logik für message_to_user bei direkter Suche) ...
            except Exception as e_crawl:
                app.logger.error(f"Fehler beim direkten Crawler-Aufruf für '{hashtag_query}': {e_crawl}")
                # ... (Ihre bestehende Logik für message_to_user bei direkter Suche) ...
        
        else: # Kein Hashtag eingegeben
            search_term_display = "Bitte geben Sie einen Hashtag ein."
            app.logger.info("Leere Hashtag-Suche erhalten.")

        # Generiere Presigned URLs für Bilder aus dem direkten Crawler-Aufruf
        if s3_client_app and images_data_from_direct_crawler:
            app.logger.info(f"Generiere Presigned URLs für {len(images_data_from_direct_crawler)} direkt abgerufene Elemente.")
            for img_data in images_data_from_direct_crawler:
                img_data_copy = img_data.copy()
                # ... (Ihre bestehende Logik zum Generieren von Presigned URLs) ...
                # Statt hier direkt `processed_template_images` zu füllen,
                # übergeben wir `current_hashtag_query_for_template`, damit JS die Daten holen kann.
                # Die direkten Ergebnisse können optional als erste schnelle Anzeige dienen.
                # Für dieses Beispiel lassen wir die JS-Logik die Galerie komplett neu aufbauen.
                pass # Die Logik zur Anzeige der direkten Ergebnisse wird durch JS ersetzt/ergänzt
    
    # Wenn ein Hashtag gesucht wurde, wird dieser an das Template übergeben,
    # damit JavaScript die Galerie-API aufrufen kann.
    return render_template("index.html",
                           images=[], # Wird initial leer sein und von JS gefüllt
                           search_term_display=search_term_display,
                           current_hashtag=current_hashtag_query_for_template, # Wichtig für JS
                           message_to_user=message_to_user)


@app.route("/api/gallery/<string:hashtag_name>")
def api_get_gallery_for_hashtag(hashtag_name):
    """
    API-Endpunkt, um alle gespeicherten Bilder für einen Hashtag aus DynamoDB abzurufen.
    """
    app.logger.info(f"API-Anfrage für Galerie des Hashtags: '{hashtag_name}'")
    processed_gallery_images = []

    if not hashtag_name:
        return jsonify({"error": "Hashtag Name ist erforderlich"}), 400

    if not crawled_media_table:
        app.logger.error("DynamoDB 'CrawledMedia' Tabelle nicht initialisiert (Import aus Crawler fehlgeschlagen?). API-Aufruf nicht möglich.")
        return jsonify({"error": "Server-Konfigurationsfehler: DynamoDB-Tabelle nicht verfügbar"}), 500

    try:
        # DynamoDB Scan Operation, um alle Items für den Hashtag zu finden
        # Beachten Sie: Ein Scan kann bei sehr großen Tabellen ineffizient sein.
        # Für Produktionsumgebungen mit vielen Daten wäre ein Global Secondary Index (GSI)
        # auf 'hashtag_source' und eine Query-Operation performanter.
        response = crawled_media_table.scan(
            FilterExpression=Attr('hashtag_source').eq(hashtag_name.lstrip('#')) & Attr('platform').eq('instagram')
        )
        items_from_db = response.get('Items', [])
        
        # Paginierung für Scan (falls die Tabelle sehr groß ist)
        while 'LastEvaluatedKey' in response:
            app.logger.info(f"Paginiere DynamoDB Scan für Hashtag '{hashtag_name}'...")
            response = crawled_media_table.scan(
                FilterExpression=Attr('hashtag_source').eq(hashtag_name.lstrip('#')) & Attr('platform').eq('instagram'),
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items_from_db.extend(response.get('Items', []))

        app.logger.info(f"DynamoDB Scan für Hashtag '{hashtag_name}' lieferte {len(items_from_db)} Elemente.")

        if s3_client_app and items_from_db:
            for item_data in items_from_db:
                item_data_copy = item_data.copy()
                bucket_to_use = item_data.get('s3_bucket', S3_BUCKET_NAME)
                s3_key_to_use = item_data.get('s3_key')

                if s3_key_to_use and bucket_to_use:
                    try:
                        presigned_url = s3_client_app.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': bucket_to_use, 'Key': s3_key_to_use},
                            ExpiresIn=3600  # 1 Stunde Gültigkeit
                        )
                        item_data_copy['display_url'] = presigned_url
                        item_data_copy['error_generating_url'] = False
                    except ClientError as e:
                        app.logger.error(f"API Galerie: ClientError Presigned URL für S3 Key '{s3_key_to_use}': {e}")
                        item_data_copy['display_url'] = '#' # Fallback
                        item_data_copy['error_generating_url'] = True
                    except Exception as e_general:
                        app.logger.error(f"API Galerie: Allgemeiner Fehler Presigned URL für S3 Key '{s3_key_to_use}': {e_general}")
                        item_data_copy['display_url'] = '#' # Fallback
                        item_data_copy['error_generating_url'] = True
                else:
                    app.logger.warning(f"API Galerie: Fehlende 's3_key' oder 's3_bucket' für Element: {item_data_copy.get('media_id')}")
                    item_data_copy['display_url'] = '#' # Fallback
                    item_data_copy['error_generating_url'] = True
                
                processed_gallery_images.append(item_data_copy)
        
        elif not s3_client_app and items_from_db:
            app.logger.error("API Galerie: S3 Client nicht initialisiert. Presigned URLs können nicht generiert werden.")
            # Füge Items ohne display_url hinzu, damit das Frontend zumindest die Metadaten hat
            for item_data in items_from_db:
                item_data_copy = item_data.copy()
                item_data_copy['display_url'] = '#'
                item_data_copy['error_generating_url'] = True
                processed_gallery_images.append(item_data_copy)


        # Sortiere Bilder, z.B. nach Download-Timestamp (optional, falls das Feld existiert und relevant ist)
        # processed_gallery_images.sort(key=lambda x: x.get('download_timestamp_utc', ''), reverse=True)

        return jsonify({
            "hashtag": hashtag_name,
            "image_count": len(processed_gallery_images),
            "images": processed_gallery_images
        }), 200

    except ClientError as e:
        app.logger.error(f"API Galerie: DynamoDB ClientError für Hashtag '{hashtag_name}': {e}")
        return jsonify({"error": "Fehler beim Zugriff auf die Datenbank"}), 500
    except Exception as e:
        app.logger.error(f"API Galerie: Unerwarteter Fehler für Hashtag '{hashtag_name}': {e}", exc_info=True)
        return jsonify({"error": "Ein unerwarteter interner Fehler ist aufgetreten"}), 500


# Der bestehende /api/images Endpunkt kann beibehalten, angepasst oder entfernt werden,
# je nachdem, ob er noch anderweitig genutzt wird.
# Für die Webseitenanzeige wird nun /api/gallery/<hashtag> primär verwendet.
@app.route("/api/images")
def get_images_api():
    # (Ihr bestehender Code für /api/images)
    # Überlegen Sie, ob dieser Endpunkt noch benötigt wird oder ob seine Funktionalität
    # in /api/gallery integriert oder durch diese ersetzt wird.
    # Für dieses Beispiel lasse ich ihn unverändert, aber markiere ihn als potenziell redundant.
    app.logger.warning("Der Endpunkt /api/images wird möglicherweise nicht mehr aktiv für die Hauptgalerie genutzt.")
    hashtag_param = request.args.get('hashtag')
    if not hashtag_param:
        return jsonify({"error": "Hashtag Parameter erforderlich"}), 400
    # ... (Rest Ihrer /api/images Logik) ...
    # Diese Logik ist sehr ähnlich zur index Route und könnte vereinfacht werden,
    # da die Lambda nun die Hauptarbeit macht und /api/gallery die Daten aus DynamoDB holt.
    # Hier wird weiterhin der direkte Crawler aufgerufen.
    try:
        if not ACCESS_TOKEN:
            app.logger.error("API: Instagram ACCESS_TOKEN ist nicht konfiguriert!")
            return jsonify({"error": "Instagram Access Token nicht konfiguriert"}), 500

        image_data_list_raw = search_media_by_hashtag(ACCESS_TOKEN, INSTAGRAM_BUSINESS_ACCOUNT_ID, hashtag_param.lstrip('#'))
        # ... (Rest Ihrer Logik zum Verarbeiten und Zurückgeben von image_data_list_raw mit Presigned URLs) ...
        # Für Kürze hier nicht komplett wiederholt.
        return jsonify({"message": "Direkte Suche über /api/images", "results": []}), 200 # Platzhalter
    except Exception as e_crawl_api:
        app.logger.error(f"API: Fehler beim direkten Crawler-Aufruf für '{hashtag_param}': {e_crawl_api}")
        return jsonify({"error": "Fehler bei der direkten Bildersuche"}), 500


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    # ... (Ihre bestehenden Logging-Konfigurationen beim Start) ...
    if not crawled_media_table:
        app.logger.error("FATAL: DynamoDB 'CrawledMedia' Tabelle konnte nicht aus crawler.py importiert oder initialisiert werden.")
    else:
        app.logger.info(f"DynamoDB 'CrawledMedia' Tabelle ('{crawled_media_table.name}') erfolgreich für API-Nutzung referenziert.")

    app.run(debug=True, host='0.0.0.0', port=5000)

