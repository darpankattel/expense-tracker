import json
import boto3
import os
from datetime import datetime
from typing import Dict, Any, List
import logging
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
from decimal import Decimal

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
    - date__gte: Filter expenses from this date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ) - optional
    - date__lte: Filter expenses until this date (ISO format) - optional
    - category: Filter by exact category match - optional
    - search: Search in merchant name (case-insensitive, requires scan) - optional
    - limit: Number of results to return (default: 50, max: 100) - optional
    - next_token: Pagination token from previous response - optional
    """
    try:
        # Extract user ID from Cognito claims
        user_id = get_user_id_from_event(event) or "darpankattel"
        if not user_id:
            return create_error_response(401, "Unauthorized: Invalid token")

        # Parse query parameters
        query_params = event.get('queryStringParameters') or {}
        date_gte = query_params.get('date__gte')
        date_lte = query_params.get('date__lte')
        category = query_params.get('category')
        search_term = query_params.get('search')
        limit = min(int(query_params.get('limit', 50)), 100)
        next_token = query_params.get('next_token')

        # Validate and normalize date parameters
        if date_gte:
            date_gte = validate_and_normalize_date(date_gte)
            if not date_gte:
                return create_error_response(400, "Invalid date__gte format. Use YYYY-MM-DD or ISO format")

        if date_lte:
            date_lte = validate_and_normalize_date(date_lte)
            if not date_lte:
                return create_error_response(400, "Invalid date__lte format. Use YYYY-MM-DD or ISO format")

        table = dynamodb.Table(TABLE_NAME)

        # Decide between Query (efficient) or Scan (when search is needed)
        if search_term:
            # SCAN approach: needed for merchant name search
            items, last_key = scan_with_search(
                table, user_id, date_gte, date_lte, category, search_term, limit, next_token
            )
        else:
            # QUERY approach: efficient date-based filtering
            items, last_key = query_with_filters(
                table, user_id, date_gte, date_lte, category, limit, next_token
            )

        # Format response data
        expenses = []
        for item in items:
            expenses.append({
                'expense_id': item.get('expenseID'),
                'merchant_name': item.get('merchantName'),
                'category': item.get('category', 'uncategorized'),
                'amount': item.get('amount'),
                'receipt_date': item.get('receiptDate'),
                'created_at': item.get('createdAt'),
                'updated_at': item.get('updatedAt'),
                'receipt': item.get('receipt'),
                'others': item.get('others', {})
            })

        # Calculate totals
        total_amount = sum(float(expense['amount']) for expense in expenses)

        response_data = {
            'count': len(expenses),
            'total_amount': round(total_amount, 2),
            'next_token': json.dumps(last_key) if last_key else None,
            'expenses': expenses
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
            'body': json.dumps(response_data, cls=DecimalEncoder)
        }

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return create_error_response(500, "Database error occurred")

    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        return create_error_response(400, str(e))

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return create_error_response(500, "Internal server error")


def query_with_filters(table, user_id: str, date_gte: str, date_lte: str,
                       category: str, limit: int, next_token: str) -> tuple:
    """
    Use DynamoDB Query with date range filtering.
    This is efficient and uses the sort key (SK) for date filtering.
    """
    pk = f'USER#{user_id}'

    # Build the KeyConditionExpression based on date filters
    if date_gte and date_lte:
        # Range query: between two dates
        key_condition = Key('PK').eq(pk) & Key('SK').between(
            f'DATE#{date_gte}',
            # Use high Unicode char to include all expenses on end date
            f'DATE#{date_lte}#\uffff'
        )
    elif date_gte:
        # From date onwards
        key_condition = Key('PK').eq(pk) & Key('SK').gte(f'DATE#{date_gte}')
    elif date_lte:
        # Up to date
        key_condition = Key('PK').eq(pk) & Key('SK').between(
            f'DATE#',
            f'DATE#{date_lte}#\uffff'
        )
    else:
        # No date filter, get all expenses
        key_condition = Key('PK').eq(pk) & Key('SK').begins_with('DATE#')

    query_kwargs = {
        'KeyConditionExpression': key_condition,
        'Limit': limit,
        'ScanIndexForward': False  # Sort descending (newest first)
    }

    # Add category filter if provided
    if category:
        query_kwargs['FilterExpression'] = Attr('category').eq(category)

    # Add pagination token
    if next_token:
        try:
            query_kwargs['ExclusiveStartKey'] = json.loads(next_token)
        except json.JSONDecodeError:
            raise ValueError("Invalid next_token format")

    response = table.query(**query_kwargs)
    items = response.get('Items', [])
    last_evaluated_key = response.get('LastEvaluatedKey')

    return items, last_evaluated_key


def scan_with_search(table, user_id: str, date_gte: str, date_lte: str,
                     category: str, search_term: str, limit: int, next_token: str) -> tuple:
    """
    Use DynamoDB Scan when search is needed.
    This is less efficient but allows for merchant name searching.

    Note: For production with large datasets, consider:
    1. OpenSearch/Elasticsearch for full-text search
    2. A separate GSI with merchantName in the key
    3. Client-side filtering after fetching all items
    """
    pk = f'USER#{user_id}'

    # Build filter expression
    filter_expressions = [
        Attr('PK').eq(pk),
        Attr('SK').begins_with('DATE#')
    ]

    # Add date filters
    if date_gte:
        filter_expressions.append(Attr('receiptDate').gte(date_gte))
    if date_lte:
        filter_expressions.append(Attr('receiptDate').lte(date_lte))

    # Add category filter
    if category:
        filter_expressions.append(Attr('category').eq(category))

    # Add merchant search (case-insensitive contains)
    if search_term:
        filter_expressions.append(Attr('merchantName').contains(search_term))

    # Combine all filters with AND
    filter_expression = filter_expressions[0]
    for expr in filter_expressions[1:]:
        filter_expression = filter_expression & expr

    scan_kwargs = {
        'FilterExpression': filter_expression,
        'Limit': limit
    }

    # Add pagination token
    if next_token:
        try:
            scan_kwargs['ExclusiveStartKey'] = json.loads(next_token)
        except json.JSONDecodeError:
            raise ValueError("Invalid next_token format")

    response = table.scan(**scan_kwargs)
    items = response.get('Items', [])
    last_evaluated_key = response.get('LastEvaluatedKey')

    logger.warning(
        f"SCAN operation performed for user {user_id} with search term: {search_term}")

    return items, last_evaluated_key


def validate_and_normalize_date(date_str: str) -> str:
    """
    Validate and normalize date string to ISO format.
    Accepts: YYYY-MM-DD or full ISO format YYYY-MM-DDTHH:MM:SSZ
    Returns: Normalized ISO string or None if invalid
    """
    try:
        # Try parsing as date only (YYYY-MM-DD)
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Try parsing as full ISO format
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, AttributeError):
        return None


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
