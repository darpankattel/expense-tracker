import json
import boto3
import uuid
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any
import logging
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')

# Environment variables
TABLE_NAME = os.environ.get('EXPENSES_TABLE_NAME')
S3_BUCKET = os.environ.get('EXPENSES_BUCKET_NAME')
SIGNED_URL_EXPIRATION = int(os.environ.get('SIGNED_URL_EXPIRATION', '3600'))


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Create Expense Lambda Handler. Checks for valid payload, puts item to the table, and returns response.

    Expected payload:
    ```
    {
        "merchant_name": "Starbucks",
        "category": "Food",
        "amount": 25.50,
        "receipt_date": "2025-01-15",
        "notes": "Coffee"
    }
    ```
    """
    try:
        # Extract user ID, but not username from Cognito claims
        print("EVENT:\n", event)
        user_id = get_user_id_from_event(event) or "darpankattel"
        if not user_id:
            return create_error_response(401, "Unauthorized: Invalid token")

        # Parse request body
        body = json.loads(event.get('body', '{}'), parse_float=Decimal)

        # Validate required fields
        validation_error = validate_expense_data(body)
        if validation_error:
            return create_error_response(400, validation_error)

        # Generate expense ID
        expense_id = str(uuid.uuid4())
        receipt_date = body['receipt_date']

        # Create expense record
        expense_data = {
            'PK': f'USER#{user_id}',
            'SK': f'DATE#{receipt_date}#{expense_id}',
            'userID': user_id,
            'expenseID': expense_id,
            'merchantName': body['merchant_name'],
            'category': body.get('category', 'uncategorized'),
            'amount': body['amount'],
            'receiptDate': receipt_date,
            'createdAt': datetime.now().isoformat(),
        }
        if body.get('others') is not None:
            expense_data['others'] = body.get('others', {})

        # Save to DynamoDB
        table = dynamodb.Table(TABLE_NAME)
        table.put_item(Item=expense_data)

        response_data = {
            'expenseId': expense_id,
            'message': 'Expense created successfully'
        }

        logger.info(
            f"Expense created successfully: {expense_id} for user: {user_id}")

        return {
            'statusCode': 201,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'POST,OPTIONS'
            },
            'body': response_data
        }

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return create_error_response(500, "Database error occurred")

    except json.JSONDecodeError:
        return create_error_response(400, "Invalid JSON in request body")

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return create_error_response(500, "Internal server error")


def get_user_id_from_event(event: Dict[str, Any]) -> str:
    """
    Extract the immutable Cognito user ID ('sub') from API Gateway event.
    This ID never changes for the lifetime of the user.
    """
    try:
        # API Gateway REST API authorizer
        if "requestContext" in event:
            rc = event["requestContext"]

            # Newer HTTP API (JWT authorizer)
            if "authorizer" in rc and "jwt" in rc["authorizer"]:
                claims = rc["authorizer"]["jwt"].get("claims", {})
                return claims.get("sub")

            # REST API (Cognito User Pool authorizer)
            if "authorizer" in rc:
                claims = rc["authorizer"].get("claims", {})
                return claims.get("sub")

        # If no ID available
        return None

    except Exception as e:
        logger.error(f"Error extracting Cognito user ID: {e}")
        return None


def validate_expense_data(data: Dict[str, Any]) -> str:
    """Validate expense data and return error message if invalid"""
    required_fields = ['amount', 'merchant_name', 'receipt_date']

    for field in required_fields:
        if field not in data or data[field] is None:
            return f"Missing required field: {field}"

    # Validate amount
    try:
        amount = float(data['amount'])
        if amount <= 0:
            return "Amount must be greater than 0"
        if amount > 9_99_999.99:
            return "Amount too large"
    except (ValueError, TypeError):
        return "Invalid amount format"

    # Validate receipt_date format (YYYY-MM-DD)
    try:
        datetime.strptime(data['receipt_date'], '%Y-%m-%d')
    except ValueError:
        return "Invalid receipt_date format. Use YYYY-MM-DD"
    # Validate merchant_name length
    if len(data['merchant_name'].strip()) == 0:
        return "Merchant cannot be empty"
    if len(data['merchant_name']) > 500:
        return "Merchant too long (max 500 characters)"

    # Validate category if provided
    if 'category' in data and len(data['category']) > 100:
        return "Category too long (max 100 characters)"

    return None


def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """Create standardized error response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'POST,OPTIONS'
        },
        'body': {
            'error': message,
            'statusCode': status_code
        }
    }


"""
{
  "body": "{\"merchant_name\": \"Starbucks\", \"category\": \"Food\", \"amount\": 25.5, \"receipt_date\": \"2025-01-15\", \"notes\": \"Coffee\"}"
}

"""
