import json

def lambda_handler(event, context):
    try:
        payload = json.loads(event['body'])
        print(f"Webhook received: {payload}")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "data": payload
            })
        }
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid payload"})
        }