import json

def lambda_handler(event, context):
    try:
        # IoT Core message format
        message = event['message']
        print(f"MQTT message received: {message}")
        
        # Process message here
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "processed"})
        }
    except KeyError:
        print("Invalid MQTT message format")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid message format"})
        }