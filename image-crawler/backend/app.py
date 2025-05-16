from flask import Flask, request, render_template, jsonify
import os
import boto3 # AWS SDK für Python
from botocore.exceptions import ClientError # Für Boto3 Fehlerbehandlung
from crawler import search_media_by_hashtag, process_media, getCreds # Stellen Sie sicher, dass diese aus crawler.py importiert werden können
import logging # Logging-Modul importieren

app = Flask(
    __name__,
    template_folder=os.path.join("..", "frontend", "templates"),
    static_folder=os.path.join("..", "frontend", "static")
)

# Instagram API Konfiguration
creds = getCreds()
ACCESS_TOKEN = creds['access_token']
INSTAGRAM_BUSINESS_ACCOUNT_ID = creds['instagram_business_id']

# AWS S3 Konfiguration aus creds (defines.py)
# Stellen Sie sicher, dass 's3_bucket_name' und 's3_bucket_region' in Ihrer defines.py korrekt definiert sind!
S3_BUCKET_NAME = creds.get('s3_bucket_name')
S3_REGION = creds.get('s3_bucket_region')

# Überprüfung der Konfiguration beim Start
if not S3_BUCKET_NAME or not S3_REGION:
    print("FEHLER: S3_BUCKET_NAME oder S3_REGION nicht in 'creds' (defines.py) gefunden oder leer. Bitte überprüfen!")
    # Im Debug-Modus könnte man hier auch einen Fehler werfen oder sys.exit() verwenden.
    # Fürs Erste lassen wir die App weiterlaufen, aber S3 wird nicht funktionieren.
    s3_client_app = None
else:
    print(f"INFO: S3 Konfiguration aus creds geladen: Bucket='{S3_BUCKET_NAME}', Region='{S3_REGION}'")
    s3_client_app = None # Wird im try-Block initialisiert
    try:
        s3_client_app = boto3.client('s3', region_name=S3_REGION)
        # Das Logging hierfür erfolgt jetzt im if __name__ == "__main__" Block,
        # nachdem das globale Logging konfiguriert wurde.
    except Exception as e:
        print(f"Schwerwiegender Fehler bei der Initialisierung des S3-Clients in app.py: {e}")


@app.route("/", methods=["GET", "POST"])
def index():
    images_data_from_crawler = []
    processed_template_images = []
    search_term_display = ""
    current_hashtag_query = ""

    if request.method == "POST":
        hashtag_query = request.form.get("hashtag", "").strip()
        current_hashtag_query = hashtag_query

        if hashtag_query:
            search_term_display = f"Ergebnisse für Hashtag: #{hashtag_query.lstrip('#')}"
            app.logger.info(f"Starte Hashtag-Suche für: {hashtag_query}")
            images_data_from_crawler = search_media_by_hashtag(
                ACCESS_TOKEN,
                INSTAGRAM_BUSINESS_ACCOUNT_ID,
                hashtag_query
            )
            app.logger.info(f"Rohdaten vom Crawler für '{hashtag_query}': {images_data_from_crawler}")
        else:
            search_term_display = "Bitte geben Sie einen Hashtag ein."
            app.logger.info("Leere Hashtag-Suche erhalten.")

    if s3_client_app and images_data_from_crawler:
        app.logger.info(f"Generiere Presigned URLs für {len(images_data_from_crawler)} Elemente.")
        for img_data in images_data_from_crawler:
            img_data_copy = img_data.copy()
            # Der Bucket-Name sollte jetzt immer S3_BUCKET_NAME sein, da der Crawler ihn auch so verwendet.
            # Wir nehmen ihn trotzdem aus img_data, falls der Crawler ihn dynamisch setzt.
            bucket_to_use = img_data.get('s3_bucket', S3_BUCKET_NAME) # Fallback auf globalen Bucket-Namen
            s3_key_to_use = img_data.get('s3_key')

            if s3_key_to_use and bucket_to_use:
                if bucket_to_use != S3_BUCKET_NAME: # Nur zur Info loggen, falls Abweichung
                    app.logger.warning(f"Bucket-Name im img_data ('{bucket_to_use}') weicht von globalem S3_BUCKET_NAME ('{S3_BUCKET_NAME}') ab. Verwende Wert aus img_data.")
                
                try:
                    presigned_url = s3_client_app.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_to_use, 'Key': s3_key_to_use},
                        ExpiresIn=3600
                    )
                    img_data_copy['display_url'] = presigned_url
                    img_data_copy['error_generating_url'] = False
                    app.logger.debug(f"Generierte Presigned URL für s3://{bucket_to_use}/{s3_key_to_use}: {presigned_url}")
                except ClientError as e:
                    app.logger.error(f"ClientError beim Generieren der Presigned URL für S3 Key '{s3_key_to_use}' im Bucket '{bucket_to_use}': {e}")
                    img_data_copy['display_url'] = '#'
                    img_data_copy['error_generating_url'] = True
                except Exception as e_general:
                    app.logger.error(f"Allgemeiner Fehler beim Generieren der Presigned URL für S3 Key '{s3_key_to_use}' im Bucket '{bucket_to_use}': {e_general}")
                    img_data_copy['display_url'] = '#'
                    img_data_copy['error_generating_url'] = True
            else:
                app.logger.warning(f"Fehlende 's3_key' ('{s3_key_to_use}') oder 's3_bucket' ('{bucket_to_use}') für Medienelement: {img_data_copy.get('media_id')}. Daten: {img_data}")
                img_data_copy['display_url'] = '#'
                img_data_copy['error_generating_url'] = True
            
            processed_template_images.append(img_data_copy)
    elif not s3_client_app and images_data_from_crawler:
        app.logger.error("S3 Client nicht initialisiert (oder Fehler bei Initialisierung). Presigned URLs können nicht generiert werden.")
        for img_data in images_data_from_crawler:
            img_data_copy = img_data.copy()
            img_data_copy['display_url'] = '#'
            img_data_copy['error_generating_url'] = True
            processed_template_images.append(img_data_copy)

    app.logger.info(f"Daten, die an das Template 'index.html' übergeben werden (Variable 'images'): {processed_template_images}")
    return render_template("index.html", images=processed_template_images, search_term_display=search_term_display, current_hashtag=current_hashtag_query)

@app.route("/api/images")
def get_images_api():
    # (Diese Funktion bleibt strukturell gleich, profitiert aber von den Änderungen oben)
    hashtag_param = request.args.get('hashtag')
    image_data_list_raw = []
    processed_api_images = []

    if hashtag_param:
        image_data_list_raw = search_media_by_hashtag(
            ACCESS_TOKEN,
            INSTAGRAM_BUSINESS_ACCOUNT_ID,
            hashtag_param
        )
        app.logger.info(f"API: Rohdaten vom Crawler für '{hashtag_param}': {image_data_list_raw}")
    else:
        return jsonify({"error": "Hashtag Parameter erforderlich"}), 400

    if s3_client_app and image_data_list_raw:
        for img_data in image_data_list_raw:
            img_data_copy = img_data.copy()
            bucket_to_use = img_data.get('s3_bucket', S3_BUCKET_NAME)
            s3_key_to_use = img_data.get('s3_key')

            if s3_key_to_use and bucket_to_use:
                try:
                    presigned_url = s3_client_app.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_to_use, 'Key': s3_key_to_use},
                        ExpiresIn=3600
                    )
                    app.logger.debug(f"API: Generierte Presigned URL für s3://{bucket_to_use}/{s3_key_to_use}: {presigned_url}")
                    processed_api_images.append({
                        "media_id": img_data_copy.get("media_id"),
                        "display_url": presigned_url,
                        "caption": img_data_copy.get("caption"),
                        "permalink": img_data_copy.get("permalink")
                    })
                except ClientError as e:
                    app.logger.error(f"API: ClientError Presigned URL für S3 Key '{s3_key_to_use}': {e}")
                except Exception as e_general:
                    app.logger.error(f"API: Allgemeiner Fehler Presigned URL für S3 Key '{s3_key_to_use}': {e_general}")
            else:
                 app.logger.warning(f"API: Fehlende 's3_key' ('{s3_key_to_use}') oder 's3_bucket' ('{bucket_to_use}') für Medienelement: {img_data_copy.get('media_id')}")
    elif not s3_client_app and image_data_list_raw:
         app.logger.error("API: S3 Client nicht initialisiert.")
         return jsonify({"error": "Server-Fehler bei der Bildverarbeitung"}), 500

    return jsonify(processed_api_images)

if __name__ == "__main__":
    # Logging global konfigurieren
    # Format angepasst für bessere Lesbarkeit und um den Logger-Namen (z.B. 'werkzeug' oder 'app') anzuzeigen
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    
    # Log-Status des S3-Clients nach der Konfiguration des Loggings
    if s3_client_app:
        app.logger.info(f"S3 client for app initialisiert für Region '{S3_REGION}' und Bucket '{S3_BUCKET_NAME}'.")
    else:
        app.logger.error(f"S3 client for app konnte NICHT initialisiert werden. Überprüfen Sie S3_BUCKET_NAME ('{S3_BUCKET_NAME}'), S3_REGION ('{S3_REGION}') in defines.py und AWS Credentials.")

    app.run(debug=True, host='0.0.0.0', port=5000)
