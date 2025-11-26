import json
import boto3
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List
import logging
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
TABLE_NAME = os.environ.get('EXPENSES_TABLE_NAME')


class DecimalEncoder(json.JSONEncoder):
    """Helper class to convert Decimal to float for JSON serialization"""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    List Expenses Lambda Handler

    Query parameters:
    - startDate: Filter expenses from this date (YYYY-MM-DD) - optional
    - endDate: Filter expenses until this date (YYYY-MM-DD) - optional
    - category: Filter by category - optional
    - limit: Number of results to return (default: 50, max: 100) - optional
    - lastEvaluatedKey: Pagination token - optional
    """
    try:
        # Extract user ID from Cognito claims
        user_id = get_user_id_from_event(event) or "darpankattel"
        if not user_id:
            return create_error_response(401, "Unauthorized: Invalid token")

        # Parse query parameters
        query_params = event.get('queryStringParameters') or {}
        start_date = query_params.get('startDate')
        end_date = query_params.get('endDate')
        category = query_params.get('category')
        limit = min(int(query_params.get('limit', 50)), 100)
        last_evaluated_key = query_params.get('lastEvaluatedKey')

        # Validate date parameters
        if start_date:
            try:
                datetime.strptime(start_date, '%Y-%m-%d')
            except ValueError:
                return create_error_response(400, "Invalid startDate format. Use YYYY-MM-DD")

        if end_date:
            try:
                datetime.strptime(end_date, '%Y-%m-%d')
            except ValueError:
                return create_error_response(400, "Invalid endDate format. Use YYYY-MM-DD")

        # Query DynamoDB
        table = dynamodb.Table(TABLE_NAME)

        # Build query parameters
        query_kwargs = {
            'KeyConditionExpression': Key('PK').eq(f'USER#{user_id}') & Key('SK').begins_with('EXPENSE#'),
            'Limit': limit,
            'ScanIndexForward': False  # Sort by SK descending (newest first)
        }

        # Add pagination token if provided
        if last_evaluated_key:
            try:
                query_kwargs['ExclusiveStartKey'] = json.loads(
                    last_evaluated_key)
            except json.JSONDecodeError:
                return create_error_response(400, "Invalid lastEvaluatedKey format")

        # Execute query
        response = table.query(**query_kwargs)
        items = response.get('Items', [])

        # Apply filters
        filtered_items = filter_expenses(items, start_date, end_date, category)

        # Format response data
        expenses = []
        for item in filtered_items:
            expenses.append({
                'expenseId': item.get('expenseId'),
                'amount': item.get('amount'),
                'merchant': item.get('merchant'),
                'notes': item.get('notes'),
                'category': item.get('category', 'uncategorized'),
                'date': item.get('date'),
                'ocrStatus': item.get('ocrStatus'),
                'createdAt': item.get('createdAt'),
                'updatedAt': item.get('updatedAt')
            })

        # Calculate totals
        total_amount = sum(float(expense['amount']) for expense in expenses)

        response_data = {
            'expenses': expenses,
            'count': len(expenses),
            'totalAmount': round(total_amount, 2),
            'lastEvaluatedKey': json.dumps(response.get('LastEvaluatedKey')) if 'LastEvaluatedKey' in response else None
        }

        logger.info(f"Retrieved {len(expenses)} expenses for user: {user_id}")

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'GET,OPTIONS'
            },
            'body': response_data
        }

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return create_error_response(500, "Database error occurred")

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


def filter_expenses(items: List[Dict], start_date: str = None, end_date: str = None, category: str = None) -> List[Dict]:
    """Filter expenses based on date range and category"""
    filtered = items

    if start_date:
        filtered = [item for item in filtered if item.get(
            'date', '') >= start_date]

    if end_date:
        filtered = [item for item in filtered if item.get(
            'date', '') <= end_date]

    if category:
        filtered = [item for item in filtered if item.get(
            'category', '').lower() == category.lower()]

    return filtered


def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """Create standardized error response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'GET,OPTIONS'
        },
        'body': json.dumps({
            'error': message,
            'statusCode': status_code
        })
    }
