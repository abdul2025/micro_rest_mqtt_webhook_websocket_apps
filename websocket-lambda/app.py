import json

def lambda_handler(event, context):
    route_key = event.get('requestContext', {}).get('routeKey')
    
    if route_key == '$connect':
        return {'statusCode': 200}
        
    elif route_key == '$disconnect':
        return {'statusCode': 200}
        
    elif route_key == '$default':
        body = json.loads(event.get('body', '{}'))
        return {
            'statusCode': 200,
            'body': json.dumps({'message': f"Echo: {body}"})
        }
    
    return {'statusCode': 400}