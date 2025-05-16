import requests
import json
import os
from io import BytesIO
from PIL import Image
import logging
from defines import getCreds
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
import datetime # Für Timestamps

# Konfigurationen laden
creds = getCreds()
API_BASE_URL = creds.get('endpoint_base')
S3_BUCKET_NAME = creds.get('s3_bucket_name')
S3_IMAGE_PREFIX = creds.get('s3_image_prefix', 'images/instagram/') # Fallback, falls nicht in creds
DYNAMODB_CRAWLEDMEDIA_TABLE_NAME = creds.get('dynamodb_crawledmedia_table')
DYNAMODB_CRAWLTASKS_TABLE_NAME = creds.get('dynamodb_crawltasks_table') # Optional für spätere Nutzung
DYNAMODB_REGION = creds.get('dynamodb_region', creds.get('s3_bucket_region')) # Fallback auf S3 Region

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

# AWS Clients auf Modulebene initialisieren
s3_client = None
dynamodb_resource = None
crawled_media_table = None
# crawl_tasks_table = None # Für spätere Nutzung

try:
    if S3_BUCKET_NAME and DYNAMODB_REGION: # DYNAMODB_REGION wird auch für S3 Client verwendet für Konsistenz
        s3_client = boto3.client('s3', region_name=DYNAMODB_REGION)
        logging.info(f"S3-Client erfolgreich initialisiert für Region {DYNAMODB_REGION}.")
    else:
        logging.error("S3_BUCKET_NAME oder DYNAMODB_REGION (verwendet für S3 Client) nicht in creds gefunden. S3-Operationen könnten fehlschlagen.")

    if DYNAMODB_CRAWLEDMEDIA_TABLE_NAME and DYNAMODB_REGION:
        dynamodb_resource = boto3.resource('dynamodb', region_name=DYNAMODB_REGION)
        crawled_media_table = dynamodb_resource.Table(DYNAMODB_CRAWLEDMEDIA_TABLE_NAME)
        logging.info(f"DynamoDB Resource und Tabelle '{DYNAMODB_CRAWLEDMEDIA_TABLE_NAME}' initialisiert für Region {DYNAMODB_REGION}.")
        # if DYNAMODB_CRAWLTASKS_TABLE_NAME:
        # crawl_tasks_table = dynamodb_resource.Table(DYNAMODB_CRAWLTASKS_TABLE_NAME)
        # logging.info(f"DynamoDB Tabelle '{DYNAMODB_CRAWLTASKS_TABLE_NAME}' initialisiert.")
    else:
        logging.error("DYNAMODB_CRAWLEDMEDIA_TABLE_NAME oder DYNAMODB_REGION nicht in creds gefunden. DynamoDB-Operationen werden fehlschlagen.")

except (NoCredentialsError, PartialCredentialsError):
    logging.error("AWS-Anmeldeinformationen nicht gefunden/unvollständig. AWS-Operationen werden fehlschlagen.")
except Exception as e:
    logging.error(f"Fehler bei der Initialisierung von AWS Clients: {e}. AWS-Operationen werden fehlschlagen.")


def get_utc_timestamp():
    return datetime.datetime.utcnow().isoformat() + "Z"

def get_user_media(access_token, user_id):
    # (Funktion bleibt im Wesentlichen gleich wie zuvor)
    url = f"{API_BASE_URL}{user_id}/media?fields=id,caption,media_type,media_url,permalink&access_token={access_token}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"HTTP-Fehler bei get_user_media für {user_id}: {e}")
        return None
    # ... weitere Fehlerbehandlung ...
    except Exception as e:
        logging.error(f"Unerwarteter Fehler bei get_user_media für {user_id}: {e}")
        return None


def download_image_to_s3(media_url, filename_base):
    if not s3_client:
        logging.error("S3-Client nicht initialisiert in download_image_to_s3. Upload nicht möglich.")
        return None
    # (Funktion bleibt im Wesentlichen gleich wie zuvor, lädt nach S3 hoch und gibt s3_key zurück)
    try:
        response = requests.get(media_url, stream=True, timeout=10)
        response.raise_for_status()
        image_content = BytesIO(response.content)
        image = Image.open(image_content)
        if image.mode in ("RGBA", "P"): image = image.convert("RGB")
        
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        
        s3_key_path = f"{S3_IMAGE_PREFIX.strip('/')}/{filename_base}.jpg"
        
        s3_client.upload_fileobj(buffer, S3_BUCKET_NAME, s3_key_path, ExtraArgs={'ContentType': 'image/jpeg'})
        logging.info(f"Bild erfolgreich nach S3 hochgeladen: s3://{S3_BUCKET_NAME}/{s3_key_path}")
        return s3_key_path
    except requests.exceptions.RequestException as e:
        logging.error(f"Fehler beim Herunterladen (requests) von {media_url}: {e}")
    except IOError as e:
        logging.error(f"Fehler beim Verarbeiten (PIL) von Bild von {media_url}: {e}")
    except ClientError as e:
        logging.error(f"AWS S3 Client Fehler beim Hochladen für {media_url}: {e}")
    except Exception as e:
        logging.error(f"Unerwarteter Fehler in download_image_to_s3 für {media_url}: {e}")
    return None


def process_media(access_token, user_id_of_account_owner):
    logging.info(f"Starte Verarbeitung eigener Medien für User-ID: {user_id_of_account_owner}")
    if not crawled_media_table:
        logging.error("DynamoDB 'CrawledMedia' Tabelle nicht initialisiert. Verarbeitung eigener Medien nicht möglich.")
        return []

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
                # Prüfen, ob Bild schon in DynamoDB ist
                try:
                    response = crawled_media_table.get_item(Key={'media_id': media_id})
                    if 'Item' in response:
                        logging.info(f"    Bild {media_id} (eigen) bereits in DynamoDB. Überspringe.")
                        # Optional: Bestehende Daten aus DynamoDB zur Liste hinzufügen, wenn Anzeige aktualisiert werden soll
                        # processed_own_images.append(response['Item']) # Stellt sicher, dass alle Keys vorhanden sind
                        continue 
                except ClientError as e_db:
                    logging.error(f"    Fehler beim Prüfen von media_id {media_id} (eigen) in DynamoDB: {e_db}. Verarbeite trotzdem.")

                media_url = media_item.get('media_url')
                if not media_url: continue

                own_image_count += 1
                filename_base_for_s3 = f"user_{user_id_of_account_owner}_{media_id}"
                s3_object_key = download_image_to_s3(media_url, filename_base_for_s3)

                if s3_object_key:
                    image_info = {
                        'media_id': media_id,
                        's3_key': s3_object_key,
                        's3_bucket': S3_BUCKET_NAME,
                        'hashtag_source': '__USER_MEDIA__', # Kennzeichnung
                        'permalink': media_item.get('permalink', ''),
                        'caption': media_item.get('caption', ''),
                        'media_url_original': media_url,
                        'download_timestamp_utc': get_utc_timestamp(),
                        'platform': 'instagram',
                        'is_hashtag_result': False
                    }
                    try:
                        crawled_media_table.put_item(Item=image_info)
                        logging.info(f"    Metadaten für eigenes Bild {media_id} in DynamoDB gespeichert.")
                        processed_own_images.append(image_info)
                    except ClientError as e_db_put:
                        logging.error(f"    Fehler beim Speichern von Metadaten für eigenes Bild {media_id} in DynamoDB: {e_db_put}")
                else:
                    logging.warning(f"    Hochladen des eigenen IMAGE-Medienelements (ID: {media_id}) nach S3 fehlgeschlagen.")
            # ... (andere Medientypen)
        logging.info(f"Verarbeitung eigener Medien abgeschlossen. {len(processed_own_images)} neue Bilder verarbeitet.")
    # ... (Fehlerbehandlung für media_api_data)
    return processed_own_images


def get_hashtag_id(access_token, user_id_making_request, hashtag_name):
    # (Funktion bleibt im Wesentlichen gleich wie zuvor)
    clean_hashtag_name = hashtag_name.strip().lstrip('#')
    if not clean_hashtag_name: return None
    url = f"{API_BASE_URL}ig_hashtag_search?user_id={user_id_making_request}&q={clean_hashtag_name}&access_token={access_token}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('data') and data['data']:
            return data['data'][0]['id']
    except Exception as e:
        logging.error(f"Fehler bei get_hashtag_id für '{clean_hashtag_name}': {e}")
    return None

def get_media_for_hashtag(access_token, user_id_making_request, hashtag_id, search_type="recent_media", limit=7):
    # (Funktion bleibt im Wesentlichen gleich wie zuvor)
    fields = "id,caption,media_type,media_url,permalink,timestamp"
    url = f"{API_BASE_URL}{hashtag_id}/{search_type}?user_id={user_id_making_request}&fields={fields}&limit={limit}&access_token={access_token}"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Fehler bei get_media_for_hashtag für ID '{hashtag_id}': {e}")
    return None


def search_media_by_hashtag(access_token, user_id_for_api_calls, hashtag_query, search_type="recent_media", limit_per_hashtag=7):
    logging.info(f"Starte Hashtag-Suche für: '{hashtag_query}', Typ: {search_type}, Limit: {limit_per_hashtag}")
    if not crawled_media_table:
        logging.error("DynamoDB 'CrawledMedia' Tabelle nicht initialisiert. Hashtag-Suche nicht möglich.")
        return []

    clean_hashtag_query = hashtag_query.strip().lstrip('#')
    if not clean_hashtag_query: return []

    hashtag_id = get_hashtag_id(access_token, user_id_for_api_calls, clean_hashtag_query)
    if not hashtag_id: return []

    media_api_data = get_media_for_hashtag(access_token, user_id_for_api_calls, hashtag_id, search_type, limit_per_hashtag)
    processed_images = [] # Bilder, die in DIESEM Durchlauf neu verarbeitet wurden

    if media_api_data and media_api_data.get('data'):
        total_items_from_api = len(media_api_data['data'])
        logging.info(f"API lieferte {total_items_from_api} Medienelemente für Hashtag '{clean_hashtag_query}' (ID: {hashtag_id}).")
        
        newly_processed_image_count = 0
        for i, media_item in enumerate(media_api_data['data']):
            media_id = media_item.get('id')
            media_type = media_item.get('media_type')
            logging.info(f"  [Item {i+1}/{total_items_from_api}] ID: {media_id}, Typ: {media_type}")

            if media_type == 'IMAGE':
                # Prüfen, ob Bild schon in DynamoDB ist
                try:
                    response = crawled_media_table.get_item(Key={'media_id': media_id})
                    if 'Item' in response:
                        logging.info(f"    Bild {media_id} bereits in DynamoDB. Überspringe Download/Upload.")
                        # Optional: Bestehendes Item zur Rückgabeliste hinzufügen, wenn app.py alle bekannten anzeigen soll
                        # processed_images.append(response['Item'])
                        continue 
                except ClientError as e_db:
                    logging.error(f"    Fehler beim Prüfen von media_id {media_id} in DynamoDB: {e_db}. Verarbeite trotzdem (potenzielles Duplikat).")

                media_url = media_item.get('media_url')
                if not media_url: continue

                filename_base_for_s3 = f"hashtag_{clean_hashtag_query}_{media_id}"
                s3_object_key = download_image_to_s3(media_url, filename_base_for_s3)

                if s3_object_key:
                    newly_processed_image_count +=1
                    image_info = {
                        'media_id': media_id,
                        's3_key': s3_object_key,
                        's3_bucket': S3_BUCKET_NAME,
                        'hashtag_source': clean_hashtag_query,
                        'permalink': media_item.get('permalink', ''),
                        'caption': media_item.get('caption', ''),
                        'media_url_original': media_url,
                        'download_timestamp_utc': get_utc_timestamp(),
                        'platform': 'instagram',
                        'is_hashtag_result': True
                    }
                    try:
                        crawled_media_table.put_item(Item=image_info)
                        logging.info(f"    Metadaten für Bild {media_id} in DynamoDB gespeichert.")
                        processed_images.append(image_info) # Nur neu verarbeitete zur Liste hinzufügen
                    except ClientError as e_db_put:
                        logging.error(f"    Fehler beim Speichern von Metadaten für {media_id} in DynamoDB: {e_db_put}")
                else:
                    logging.warning(f"    Hochladen des IMAGE (ID: {media_id}) nach S3 fehlgeschlagen.")
            # ... (andere Medientypen)
        logging.info(f"Hashtag-Suche abgeschlossen. {newly_processed_image_count} NEUE Bilder von {total_items_from_api} API-Elementen für '{clean_hashtag_query}' verarbeitet und in DynamoDB gespeichert.")
    # ... (Fehlerbehandlung für media_api_data)
    
    # Optional: Update CrawlTasks table
    # if crawl_tasks_table and DYNAMODB_CRAWLTASKS_TABLE_NAME:
    # try:
    # crawl_tasks_table.update_item(
    # Key={'search_term': clean_hashtag_query, 'platform': 'instagram'}, # Falls platform Teil des Keys ist
    # UpdateExpression="set #s = :status, last_completed_timestamp_utc = :ts",
    # ExpressionAttributeNames={'#s': 'status'},
    # ExpressionAttributeValues={':status': 'completed', ':ts': get_utc_timestamp()}
    # )
    # logging.info(f"CrawlTask für '{clean_hashtag_query}' als 'completed' markiert.")
    # except ClientError as e_task:
    # logging.error(f"Fehler beim Aktualisieren von CrawlTask für '{clean_hashtag_query}': {e_task}")
            
    return processed_images # Gibt nur die in DIESEM Durchlauf neu verarbeiteten Bilder zurück

if __name__ == '__main__':
    # (Testblock bleibt ähnlich, testet jetzt die DynamoDB-Interaktionen implizit)
    test_access_token = creds.get('access_token')
    test_instagram_business_id = creds.get('instagram_business_id')

    if not all([test_access_token, test_instagram_business_id, S3_BUCKET_NAME, DYNAMODB_CRAWLEDMEDIA_TABLE_NAME, DYNAMODB_REGION]):
        logging.error("Einige erforderliche Konfigurationen (Token, IDs, S3, DynamoDB) fehlen in defines.py für den Testlauf.")
    else:
        print("\n--- Teste Abruf eigener Medien (mit DynamoDB Check/Write) ---")
        eigen_images = process_media(test_access_token, test_instagram_business_id)
        print(f"{len(eigen_images)} eigene Bilder in diesem Durchlauf neu verarbeitet/gespeichert (Details im Log).")

        print("\n--- Teste Hashtag-Suche (mit DynamoDB Check/Write) ---")
        test_hashtag = "sonnenuntergang"
        hashtag_images = search_media_by_hashtag(test_access_token, test_instagram_business_id, test_hashtag, limit_per_hashtag=5)
        print(f"{len(hashtag_images)} Bilder für Hashtag #{test_hashtag} in diesem Durchlauf neu verarbeitet/gespeichert (Details im Log).")
