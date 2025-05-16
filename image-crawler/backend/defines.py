import requests
import json

def getCreds():
    creds = dict()
    creds['access_token'] = 'EAAULuwLl9tIBO1uB6rjALzxaxBBsi6EFLiL74VmeRu56CzODVgpOaZAIkWVUW4HRPZBGcLWBlRqu5bCiv4DhYf7ZBNgRerZC1Q8jfySg9h72w24MD47wa7cDBFxBrup1rKVZClbMatorM809EkcKTFOEfyfYzQ0U0YGopozd0vy2iZBFSLOZAuH7LHARfQmkf2FDhprX8kzGXkUxzdNsBVLH9HzDSHbScYgkqEZD'
    creds['client_id'] = '1420272718968530'
    creds['client_secret'] = 'b52ab6be3afef6852cbd05889699c1df'
    creds['instagram_business_id'] ='17841473932022135'
    creds['graph_domain'] = 'https://graph.facebook.com/'
    creds['s3_bucket_name'] = 'image-crawler-image-store-s3'
    creds['s3_bucket_region'] ='eu-central-1'
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