import requests
import json
import os
from io import BytesIO
from PIL import Image
import logging
from defines import getCreds # Importiere die Funktion
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

# Rufe die Funktion auf, um die Zugangsdaten zu bekommen.
creds = getCreds()

# Instagram Graph API Konfiguration
API_BASE_URL = creds['endpoint_base']

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# AWS S3 Konfiguration - BITTE ERSETZEN SIE DIESE PLATZHALTER, FALLS NOCH NICHT GESCHEHEN!
S3_BUCKET_NAME = 'image-crawler-image-store-s3' # Stellen Sie sicher, dass dies Ihr korrekter Bucket-Name ist
S3_IMAGE_PREFIX = 'images/instagram/' # Zielordner-Präfix in S3

# S3 Client auf Modulebene initialisieren
s3_client = None
try:
    s3_client = boto3.client('s3') # Region kann hier auch angegeben werden, z.B. boto3.client('s3', region_name='ihre-region')
    logging.info("S3-Client erfolgreich initialisiert.")
except (NoCredentialsError, PartialCredentialsError):
    logging.error("AWS-Anmeldeinformationen nicht gefunden oder unvollständig. Bitte konfigurieren (z.B. via 'aws configure'). S3-Operationen werden fehlschlagen.")
except Exception as e:
    logging.error(f"Fehler bei der Initialisierung des S3-Clients: {e}. S3-Operationen werden fehlschlagen.")


def get_user_media(access_token, user_id):
    """Ruft Medien (Bilder/Videos) des EIGENEN Instagram-Benutzers ab."""
    url = f"{API_BASE_URL}{user_id}/media?fields=id,caption,media_type,media_url,permalink&access_token={access_token}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP-Fehler beim Abrufen von Benutzermedien für User-ID {user_id}: {e}. Status: {e.response.status_code}. Text: {e.response.text}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Netzwerkfehler beim Abrufen von Benutzermedien für User-ID {user_id}: {e}")
        return None
    except json.JSONDecodeError as e:
        response_text = getattr(response, 'text', 'Kein Antworttext verfügbar')
        logging.error(f"JSON-Dekodierungsfehler beim Abrufen von Benutzermedien für User-ID {user_id}: {e}, Antworttext: {response_text}")
        return None
    except Exception as e:
        logging.error(f"Unerwarteter Fehler beim Abrufen von Benutzermedien für User-ID {user_id}: {e}")
        return None

def download_image(media_url, filename_base): # local_dir_fallback entfernt, da wir primär S3 nutzen
    """
    Lädt ein Bild von einer URL herunter und lädt es nach AWS S3 hoch.
    Gibt die S3-Key oder None bei Fehler zurück.
    filename_base ist z.B. "hashtag_natur_MEDIAID" oder "user_USERID_MEDIAID" (ohne .jpg)
    """
    if not s3_client:
        logging.error("S3-Client nicht initialisiert. Upload nach S3 nicht möglich.")
        return None

    try:
        response = requests.get(media_url, stream=True, timeout=10)
        response.raise_for_status()

        image_content = BytesIO(response.content)
        image = Image.open(image_content)

        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")

        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)

        s3_key = f"{S3_IMAGE_PREFIX.strip('/')}/{filename_base}.jpg"

        s3_client.upload_fileobj(
            buffer,
            S3_BUCKET_NAME,
            s3_key,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
        logging.info(f"Bild erfolgreich nach S3 hochgeladen: s3://{S3_BUCKET_NAME}/{s3_key}")
        return s3_key

    except requests.exceptions.RequestException as e:
        logging.error(f"Fehler beim Herunterladen des Bildes von {media_url}: {e}")
        return None
    except IOError as e:
        logging.error(f"Fehler beim Verarbeiten des Bildes von {media_url}: {e}")
        return None
    except ClientError as e:
        logging.error(f"AWS S3 Client Fehler beim Hochladen für {media_url} zu s3://{S3_BUCKET_NAME}/{s3_key if 's3_key' in locals() else filename_base}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unerwarteter Fehler beim Herunterladen/Hochladen des Bildes {media_url}: {e}")
        return None

# --- DEFINITION VON process_media ---
def process_media(access_token, user_id_of_account_owner):
    """
    Verarbeitet Medien des EIGENEN Instagram-Benutzers, lädt Bilder nach S3 hoch und gibt Daten zurück.
    """
    logging.info(f"Starte Verarbeitung eigener Medien für User-ID: {user_id_of_account_owner}")
    media_api_data = get_user_media(access_token, user_id_of_account_owner)
    processed_own_images = []

    if media_api_data and media_api_data.get('data'):
        total_own_items = len(media_api_data['data'])
        logging.info(f"API lieferte {total_own_items} eigene Medienelemente für User-ID {user_id_of_account_owner}.")
        
        own_image_count = 0
        for i, media_item in enumerate(media_api_data['data']):
            media_id = media_item.get('id')
            media_type = media_item.get('media_type')
            logging.info(f"  [Eigenes Item {i+1}/{total_own_items}] ID: {media_id}, Typ: {media_type}")

            if media_type == 'IMAGE':
                own_image_count += 1
                media_url = media_item.get('media_url')

                if not media_url or not media_id:
                    logging.warning(f"    Überspringe eigenes IMAGE-Medienelement (ID: {media_id}) aufgrund fehlender URL.")
                    continue
                
                caption = media_item.get('caption', '')
                permalink = media_item.get('permalink', '')
                filename_base_for_s3 = f"user_{user_id_of_account_owner}_{media_id}" # Eindeutiger Name für eigene Bilder
                
                s3_object_key = download_image(media_url, filename_base_for_s3)

                if s3_object_key:
                    image_info = {
                        "media_id": media_id,
                        "s3_key": s3_object_key,
                        "s3_bucket": S3_BUCKET_NAME,
                        "caption": caption,
                        "permalink": permalink,
                        "media_url_original": media_url,
                        "is_hashtag_result": False # Kennzeichnung als eigenes Bild
                    }
                    processed_own_images.append(image_info)
                    logging.info(f"    Verarbeitetes eigenes Bild (Nr. {own_image_count}), S3 Key: {s3_object_key}")
                else:
                    logging.warning(f"    Hochladen des eigenen IMAGE-Medienelements (ID: {media_id}, URL: {media_url}) nach S3 fehlgeschlagen.")
            # Hier könnte man auch CAROUSEL_ALBUM und VIDEO für eigene Medien behandeln
            elif media_type in ['CAROUSEL_ALBUM', 'VIDEO']:
                 logging.info(f"    Eigenes Medienelement (ID: {media_id}, Typ: {media_type}) gefunden. Verarbeitung dafür noch nicht implementiert.")
            else:
                logging.warning(f"    Unbekannter oder nicht unterstützter Typ für eigenes Medienelement (ID: {media_id}, Typ: {media_type}).")
        
        logging.info(f"Verarbeitung eigener Medien abgeschlossen. {own_image_count} Bilder von {total_own_items} API-Elementen für User-ID {user_id_of_account_owner} verarbeitet.")
    elif media_api_data and 'error' in media_api_data:
        logging.error(f"API-Fehler beim Abrufen der eigenen Medien für User-ID {user_id_of_account_owner}: {media_api_data['error']}")
    else:
        logging.warning(f"Keine eigenen Medien für User-ID {user_id_of_account_owner} gefunden oder unerwartete Antwortstruktur.")
    
    return processed_own_images


def get_hashtag_id(access_token, user_id_making_request, hashtag_name):
    """Ruft die ID eines Hashtags ab."""
    clean_hashtag_name = hashtag_name.strip().lstrip('#')
    if not clean_hashtag_name:
        logging.warning("Leerer Hashtag-Name nach Bereinigung erhalten.")
        return None
        
    url = f"{API_BASE_URL}ig_hashtag_search?user_id={user_id_making_request}&q={clean_hashtag_name}&access_token={access_token}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data.get('data') and len(data['data']) > 0:
            logging.info(f"Hashtag-ID {data['data'][0]['id']} für '{clean_hashtag_name}' gefunden.")
            return data['data'][0]['id']
        else:
            logging.warning(f"Keine ID für Hashtag '{clean_hashtag_name}' gefunden. Antwort: {data}")
            return None
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP-Fehler beim Abrufen der Hashtag-ID für '{clean_hashtag_name}': {e}. Status: {e.response.status_code}. Text: {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"Unerwarteter Fehler beim Abrufen der Hashtag-ID für '{clean_hashtag_name}': {e}")
        return None


def get_media_for_hashtag(access_token, user_id_making_request, hashtag_id, search_type="recent_media", limit=25):
    """Ruft 'recent_media' oder 'top_media' für eine Hashtag-ID ab."""
    fields = "id,caption,media_type,media_url,permalink,timestamp"
    url = f"{API_BASE_URL}{hashtag_id}/{search_type}?user_id={user_id_making_request}&fields={fields}&limit={limit}&access_token={access_token}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        logging.info(f"Medien für Hashtag-ID {hashtag_id} ({search_type}, Limit {limit}) erfolgreich abgerufen.")
        return response.json()
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP-Fehler beim Abrufen von Medien für Hashtag-ID '{hashtag_id}': {e}. Status: {e.response.status_code}. Text: {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"Unerwarteter Fehler beim Abrufen von Medien für Hashtag-ID '{hashtag_id}': {e}")
        return None

# Dies ist die korrigierte und vollständige Version von search_media_by_hashtag
def search_media_by_hashtag(access_token, user_id_for_api_calls, hashtag_query, search_type="recent_media", limit_per_hashtag=25):
    logging.info(f"Starte Hashtag-Suche für: '{hashtag_query}', Typ: {search_type}, Limit: {limit_per_hashtag}")
    clean_hashtag_query = hashtag_query.strip().lstrip('#')
    if not clean_hashtag_query:
        logging.warning("Leerer Hashtag für Suche erhalten. Breche ab.")
        return []

    hashtag_id = get_hashtag_id(access_token, user_id_for_api_calls, clean_hashtag_query)
    
    if not hashtag_id:
        logging.error(f"Konnte keine ID für Hashtag '{clean_hashtag_query}' finden. Suche wird abgebrochen.")
        return []

    # media_data wird hier korrekt zugewiesen
    media_api_data = get_media_for_hashtag(access_token, user_id_for_api_calls, hashtag_id, search_type, limit_per_hashtag)
    processed_images = []

    if media_api_data and media_api_data.get('data'):
        total_items_from_api = len(media_api_data['data'])
        logging.info(f"API lieferte {total_items_from_api} Medienelemente für Hashtag '{clean_hashtag_query}' (ID: {hashtag_id}) vor dem Filtern.")
        
        image_count = 0 

        for i, media_item in enumerate(media_api_data['data']):
            media_id_of_post = media_item.get('id')
            # media_type_from_api wird hier korrekt zugewiesen
            media_type_from_api = media_item.get('media_type')
            
            logging.info(f"  [Item {i+1}/{total_items_from_api}] ID: {media_id_of_post}, Typ: {media_type_from_api}")

            if media_type_from_api == 'IMAGE':
                image_count += 1
                media_url = media_item.get('media_url')
                
                if not media_url or not media_id_of_post:
                    logging.warning(f"    Überspringe IMAGE-Medienelement (ID: {media_id_of_post}) aufgrund fehlender URL.")
                    continue

                caption = media_item.get('caption', '')
                permalink = media_item.get('permalink', '')
                
                # filename_base für S3-Key
                filename_base_for_s3 = f"hashtag_{clean_hashtag_query}_{media_id_of_post}"
                
                s3_object_key = download_image(media_url, filename_base_for_s3)

                if s3_object_key:
                    image_info = {
                        "media_id": media_id_of_post,
                        "s3_key": s3_object_key,
                        "s3_bucket": S3_BUCKET_NAME,
                        "caption": caption,
                        "permalink": permalink,
                        "media_url_original": media_url,
                        "is_hashtag_result": True
                    }
                    processed_images.append(image_info)
                    logging.info(f"    Verarbeitetes Hashtag-Bild (Nr. {image_count}), S3 Key: {s3_object_key}")
                else:
                    logging.warning(f"    Hochladen des IMAGE-Medienelements (ID: {media_id_of_post}, URL: {media_url}) nach S3 fehlgeschlagen.")
            
            elif media_type_from_api == 'CAROUSEL_ALBUM':
                logging.info(f"    Karussell-Album (ID: {media_id_of_post}) gefunden. Verarbeitung von 'children' noch nicht implementiert.")
            
            elif media_type_from_api == 'VIDEO':
                logging.info(f"    Video (ID: {media_id_of_post}) gefunden. Wird aktuell übersprungen.")
            
            else:
                logging.warning(f"    Unbekannter oder nicht unterstützter Medientyp (ID: {media_id_of_post}, Typ: {media_type_from_api}).")

        logging.info(f"Filterung abgeschlossen. {image_count} Bilder von {total_items_from_api} API-Elementen für Hashtag '{clean_hashtag_query}' verarbeitet.")

    elif media_api_data and 'error' in media_api_data:
        logging.error(f"API-Fehler beim Abrufen von Medien für Hashtag '{clean_hashtag_query}': {media_api_data['error']}")
    else:
        logging.warning(f"Keine Medien für Hashtag '{clean_hashtag_query}' (ID: {hashtag_id}) gefunden oder unerwartete Antwortstruktur von der API.")
        
    return processed_images


if __name__ == '__main__':
    test_access_token = creds['access_token']
    test_instagram_business_id = creds['instagram_business_id']
    
    # Teste Abruf eigener Medien
    print("\n--- Teste Abruf eigener Medien ---")
    # Der Parameter local_dir wird nicht mehr an process_media übergeben, da es S3 nutzt
    eigen_images = process_media(test_access_token, test_instagram_business_id)
    if eigen_images:
        print(f"{len(eigen_images)} eigene Bilder gefunden und nach S3 verarbeitet (Details im Log).")
        # for img in eigen_images:
        # print(f"Eigenes Bild Details (S3 Key): {img.get('s3_key')}")
    else:
        print("Keine eigenen Bilder zum Anzeigen oder Fehler bei der Verarbeitung.")

    # Teste Hashtag-Suche
    print("\n--- Teste Hashtag-Suche ---")
    test_hashtag = "landschaftsfotografie" # Geändertes Beispiel-Hashtag
    # Der Parameter local_dir wird nicht mehr an search_media_by_hashtag übergeben
    hashtag_images = search_media_by_hashtag(test_access_token, test_instagram_business_id, test_hashtag, limit_per_hashtag=5)
    if hashtag_images:
        print(f"{len(hashtag_images)} Bilder für Hashtag #{test_hashtag} gefunden und nach S3 verarbeitet (Details im Log).")
        # for img in hashtag_images:
        # print(f"Hashtag Bild Details (S3 Key): {img.get('s3_key')}")
    else:
        print(f"Keine Bilder für Hashtag #{test_hashtag} zum Anzeigen oder Fehler bei der Verarbeitung.")
