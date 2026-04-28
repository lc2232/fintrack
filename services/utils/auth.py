import functools
import json

from aws_lambda_powertools import Logger

logger = Logger()


def extract_user_id(event):
    """
    Extracts the user ID (sub claim) from the API Gateway v2 event.
    Handles both raw dictionary events and Powertools event objects.
    """
    # If using Powertools APIGatewayProxyEventV2 object
    if hasattr(event, "request_context"):
        authorizer = event.request_context.authorizer
        if hasattr(authorizer, "jwt"):
            return authorizer.jwt.claims.get("sub")
        return authorizer.get("jwt", {}).get("claims", {}).get("sub")

    # If using raw dictionary
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    return authorizer.get("jwt", {}).get("claims", {}).get("sub")


def require_user(app):
    """Decorator to ensure a valid user_id is present before calling the route."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            user_id = extract_user_id(app.current_event)

            if not user_id:
                logger.error("No user_id found in authorizer claims")
                return {
                    "statusCode": 401,
                    "body": json.dumps({"message": "Unauthorized user"}),
                }

            return func(user_id, *args, **kwargs)

        return wrapper

    return decorator
