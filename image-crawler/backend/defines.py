import requests
import json

def getCreds():
    creds = dict()
    creds['access_token'] = 'EAAULuwLl9tIBO83W0KqFIlxTTChEUiBcod2JQjZBcm1gHZCOzZAoULU4xZAVU8Go7Degn0GGyDznuUyLkqeWiBxwjgWZA2sqj6rFaSlZC4Xig7sl8ZBsJf13oMzdZBKvLsTOpdEyGYLMnCJmLTSub94mp5G8zwgsDTtD1yZCrdr2pPGj1XjRk7ilmSn78dKZBL2IGNQ27vspyCl1pPF7VOT9BAHw4K5DV8oU7UWKIZD'
    creds['client_id'] = '1420272718968530'
    creds['client_secret'] = 'b52ab6be3afef6852cbd05889699c1df'
    creds['instagram_business_id'] ='17841473932022135'
    creds['graph_domain'] = 'https://graph.facebook.com/'
    creds['s3_bucket_name'] = 'image-crawler-image-store-s3'
    creds['s3_bucket_region'] ='eu-central-1'
    creds['sqs_queue_url'] = 'https://sqs.eu-north-1.amazonaws.com/546229927029/image-crawler-queue'
    creds['sqs_queue_region'] ='eu-north-1' 
    creds['ec2_ip_adress']='13.60.105.88'
    creds['ec2_instance_id'] ='i-01898c350dbd16879'
    creds['dynamodb_crawledmedia_table'] = 'image-crawler-db' # Oder wie auch immer Sie die Tabelle genannt haben
    creds['dynamodb_crawltasks_table'] = 'CrawlTasks'   # Oder wie auch immer Sie die Tabelle genannt haben
    creds['dynamodb_region'] = 'eu-north-1' 
    creds['graph_version'] = 'v22.0'
    creds['endpoint_base'] = creds['graph_domain'] + creds['graph_version'] + '/'
    creds['debug'] = 'no'
    return creds

def makeApiCall(url, endpointParams, debug = 'no'):
    data = requests.get(url, endpointParams)

    response = dict()
    response['url'] = url
    response['endpoint_params'] = endpointParams
    response['endpoint_params_pretty'] = json.dumps(endpointParams, indent=4)
    response['json_data'] = json.loads(data.content)
    response['json_data_pretty']= json.dumps(response['json_data'],indent=4)

    if('yes' ==debug):
        displayApiCallData(response)
    return response

def displayApiCallData(response):
    print ("\nURL:")
    print (response['url'])
    print ("\n Endpoint Params: ")
    print(response['endpoint_params_pretty'])
    print("\n Response: ")
    print(response['json_data_pretty'])

#short lived access_token
#EAAULuwLl9tIBO63K3kSVbmVgmrC3PAwZAKagTokT0KT8US0zBaq7oucfFWuZCCZC7k7naGnPwZBxrGof05JflAiS6CCABmOsRshZBjniZAfRI1dccuBIcZBZAAV5Kpm0O05gCtvOVUZCr5RgKQShtKarqQ4HB0dbXlbVnTYKvPkeFKEKYZCBDxZAd1iuwAgT6IwQjKSNRFHncjUZC6oWPGG3aFT8VnOZBFfHj5KuQO10ZD