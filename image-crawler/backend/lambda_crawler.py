# lambda_function.py

import json
import logging
import os

# Importiere die notwendigen Funktionen aus deinem bestehenden Crawler-Skript
# Stelle sicher, dass crawler.py und defines.py im selben Verzeichnis
# wie lambda_function.py im Deployment-Paket liegen.
from crawler import search_media_by_hashtag, getCreds, process_media

# Konfigurationen laden (aus defines.py oder Umgebungsvariablen)
# Für Lambda ist es Best Practice, Konfigurationen über Umgebungsvariablen zu managen.
# getCreds() müsste ggf. angepasst werden, um zuerst Umgebungsvariablen zu prüfen.
# Beispiel: ACCESS_TOKEN = os.environ.get('INSTAGRAM_ACCESS_TOKEN')
creds = getCreds()
ACCESS_TOKEN = creds.get('access_token')
INSTAGRAM_BUSINESS_ACCOUNT_ID = creds.get('instagram_business_id')
# S3_BUCKET_NAME, DYNAMODB_CRAWLEDMEDIA_TABLE_NAME etc. werden von crawler.py direkt aus creds bezogen.

# Logging konfigurieren
# AWS Lambda leitet print()-Anweisungen und Logging-Ausgaben an CloudWatch Logs weiter.
logger = logging.getLogger()
logger.setLevel(logging.INFO) # Setze das gewünschte Logging-Level

def lambda_handler(event, context):
    """
    AWS Lambda Handler Funktion.
    Wird durch SQS-Nachrichten ausgelöst.
    Verarbeitet jede Nachricht, um Medien für einen Hashtag zu crawlen.
    """
    logger.info(f"Lambda-Funktion gestartet. Event empfangen: {json.dumps(event)}")

    if not ACCESS_TOKEN or not INSTAGRAM_BUSINESS_ACCOUNT_ID:
        logger.error("Instagram Access Token oder Business Account ID nicht konfiguriert. Verarbeitung abgebrochen.")
        # Hier könntest du die Nachricht ggf. in eine Dead-Letter Queue (DLQ) verschieben
        # oder einen Fehler zurückgeben, damit SQS den Versuch wiederholt (abhängig von der SQS-Konfiguration).
        return {'statusCode': 500, 'body': 'Konfigurationsfehler in Lambda'}

    processed_messages = 0
    failed_messages = 0

    for record in event.get('Records', []):
        try:
            message_body_str = record.get('body')
            if not message_body_str:
                logger.warning("SQS-Nachricht ohne Body empfangen. Überspringe.")
                failed_messages += 1
                continue

            logger.info(f"Verarbeite SQS-Nachricht: {message_body_str}")
            message_body = json.loads(message_body_str)

            hashtag_query = message_body.get('hashtag')
            platform = message_body.get('platform') # Könnte für zukünftige Erweiterungen nützlich sein

            if not hashtag_query:
                logger.warning(f"Kein 'hashtag' in der Nachricht gefunden: {message_body_str}. Überspringe.")
                failed_messages += 1
                continue

            logger.info(f"Starte Hashtag-Suche aus Lambda für: '{hashtag_query}' auf Plattform '{platform}'")

            # Rufe die Hauptfunktion des Crawlers auf
            # Du könntest hier auch process_media aufrufen, wenn die SQS-Nachricht
            # stattdessen das Crawlen eigener Medien auslösen soll.
            # Für dieses Beispiel fokussieren wir uns auf die Hashtag-Suche.
            # Das Limit kann hier angepasst oder über die SQS-Nachricht/Umgebungsvariablen gesteuert werden.
            images_found = search_media_by_hashtag(
                access_token=ACCESS_TOKEN,
                user_id_for_api_calls=INSTAGRAM_BUSINESS_ACCOUNT_ID,
                hashtag_query=hashtag_query,
                limit_per_hashtag=creds.get('lambda_hashtag_limit', 25) # Beispiel: Ein höheres Limit für Lambda
            )

            logger.info(f"Hashtag-Suche für '{hashtag_query}' abgeschlossen. {len(images_found)} neue Bilder verarbeitet und in DynamoDB/S3 gespeichert.")
            processed_messages += 1

        except json.JSONDecodeError as e:
            logger.error(f"Fehler beim Parsen des JSON-Bodys der SQS-Nachricht: {message_body_str}. Fehler: {e}")
            failed_messages += 1
        except Exception as e:
            # Allgemeine Fehlerbehandlung für unerwartete Probleme während der Verarbeitung einer Nachricht
            logger.error(f"Fehler bei der Verarbeitung der SQS-Nachricht (Body: {message_body_str}): {e}", exc_info=True)
            failed_messages += 1
            # Wichtig: Wenn hier ein Fehler auftritt, wird die Nachricht (abhängig von der SQS-Konfiguration)
            # nach Ablauf der Visibility Timeout wieder sichtbar und erneut verarbeitet.
            # Eine Dead-Letter Queue (DLQ) für SQS ist hier sehr empfehlenswert, um fehlerhafte Nachrichten
            # nach mehreren erfolglosen Versuchen dorthin zu verschieben und Endlosschleifen zu vermeiden.

    logger.info(f"Lambda-Funktion abgeschlossen. Verarbeitete Nachrichten: {processed_messages}, Fehlgeschlagene Nachrichten: {failed_messages}")

    # Lambda sollte einen erfolgreichen Statuscode zurückgeben, wenn es die Batch-Verarbeitung
    # der SQS-Nachrichten (ohne kritische Fehler, die einen Abbruch erfordern) abgeschlossen hat.
    # SQS kümmert sich um das Löschen der Nachrichten aus der Queue, wenn die Lambda-Funktion
    # erfolgreich (ohne Exception) zurückkehrt und der SQS-Trigger entsprechend konfiguriert ist.
    if failed_messages > 0 and processed_messages == 0:
         # Wenn alle Nachrichten fehlgeschlagen sind, könnte man einen Fehler signalisieren,
         # um die gesamte Batch-Verarbeitung als fehlgeschlagen zu markieren (abhängig von der Fehlerstrategie).
         # Für dieses Beispiel geben wir trotzdem 200 zurück, da SQS die einzelnen fehlerhaften Nachrichten
         # erneut zustellen wird (bis zur maxReceiveCount).
        pass

    return {
        'statusCode': 200,
        'body': json.dumps(f'Verarbeitung von {processed_messages} Nachrichten abgeschlossen, {failed_messages} fehlgeschlagen.')
    }

# Für lokale Tests (optional)
if __name__ == '__main__':
    # Beispiel für ein SQS-Event-Format
    sample_event = {
        "Records": [
            {
                "messageId": "19dd0b57-b21e-4ac1-bd88-01bbb068cb78",
                "receiptHandle": "MessageReceiptHandle",
                "body": "{\"hashtag\": \"testlambda\", \"platform\": \"instagram\"}",
                "attributes": {
                    "ApproximateReceiveCount": "1",
                    "SentTimestamp": "1523232000000",
                    "SenderId": "123456789012",
                    "ApproximateFirstReceiveTimestamp": "1523232000001"
                },
                "messageAttributes": {},
                "md5OfBody": "7b270e59b47ff90a553787216d55d91d",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:eu-north-1:123456789012:MyQueue", # Beispiel ARN
                "awsRegion": "eu-north-1"
            },
            { # Beispiel für eine weitere Nachricht im Batch
                "messageId": "20ee0c57-c21e-4ac1-bd88-01bbb068cb79",
                "receiptHandle": "MessageReceiptHandle2",
                "body": "{\"hashtag\": \"sonnenuntergang\", \"platform\": \"instagram\"}",
                "awsRegion": "eu-north-1"
            }
        ]
    }
    # Stelle sicher, dass deine defines.py mit gültigen Test-Credentials gefüllt ist
    # oder die Umgebungsvariablen für lokale Tests gesetzt sind.
    if not creds.get('access_token') or not creds.get('instagram_business_id'):
        print("WARNUNG: Instagram Access Token oder Business ID nicht in defines.py für lokalen Test gefunden.")
    else:
        print("Starte lokalen Test der Lambda-Funktion...")
        lambda_handler(sample_event, None)
        print("Lokaler Test abgeschlossen.")

