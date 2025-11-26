import json
import boto3
import uuid
import os
from datetime import datetime, timedelta
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
    Create Expense Lambda Handler

    Expected payload:
    {
        "amount": 25.50,
        "merchant": "Starbucks",
        "notes": "Coffee",
        "category": "Food",
        "date": "2025-01-15"
    }
    """
    try:
        # Extract user ID from Cognito claims
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

        # Create expense record
        expense_data = {
            'PK': f'USER#{user_id}',
            'SK': f'EXPENSE#{expense_id}',
            'userId': user_id,
            'expenseId': expense_id,
            'amount': body['amount'],
            'notes': body['notes'],
            'category': body.get('category', 'uncategorized'),
            'date': body['date'],
            'ocrStatus': 'NOT_APPLICABLE',
            'createdAt': datetime.utcnow().isoformat(),
            'updatedAt': datetime.utcnow().isoformat()
        }

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
            'body': json.dumps(response_data)
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
    """Extract user ID from Cognito authorizer context"""
    try:
        # From API Gateway Lambda authorizer
        if 'requestContext' in event and 'authorizer' in event['requestContext']:
            claims = event['requestContext']['authorizer'].get('claims', {})
            return claims.get('sub') or claims.get('cognito:username')

        return None

    except Exception as e:
        logger.error(f"Error extracting user ID: {str(e)}")
        return None


def validate_expense_data(data: Dict[str, Any]) -> str:
    """Validate expense data and return error message if invalid"""
    required_fields = ['amount', 'merchant', 'date']

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

    # Validate date format (YYYY-MM-DD)
    try:
        datetime.strptime(data['date'], '%Y-%m-%d')
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD"

    # Validate merchant length
    if len(data['merchant'].strip()) == 0:
        return "Merchant cannot be empty"
    if len(data['merchant']) > 500:
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
        'body': json.dumps({
            'error': message,
            'statusCode': status_code
        })
    }


lambda_handler({
    "body": json.dumps({
        "amount": 45.67,
        "merchant": "Amazon",
        "notes": "Office supplies",
        "category": "Work",
        "date": "2025-02-20"
    }),
    "requestContext": {
        "authorizer": {
            "claims": {
                "sub": "user-1234"
            }
        }
    }
}, None)
