"""
DynamoDB service — audit report storage.
Table: amazon-audit-reports
  PK: user_id (String)
  SK: audit_id (String)
"""
import json
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings


def _client():
    return boto3.client(
        "dynamodb",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _resource():
    return boto3.resource(
        "dynamodb",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def ensure_table() -> None:
    """Create the DynamoDB table if it doesn't already exist."""
    table_name = settings.DYNAMODB_TABLE
    client = _client()
    try:
        client.describe_table(TableName=table_name)
        print(f"[dynamo] Table '{table_name}' already exists")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code != "ResourceNotFoundException":
            print(f"[dynamo] WARNING: cannot check table — {code}: {e.response['Error']['Message']}")
            return
        # ResourceNotFoundException — table doesn't exist yet, create it
        print(f"[dynamo] Creating table '{table_name}'...")
        client.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "user_id",  "KeyType": "HASH"},
                {"AttributeName": "audit_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id",  "AttributeType": "S"},
                {"AttributeName": "audit_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        print(f"[dynamo] Table '{table_name}' ready")
        return


def save_audit(user_id: str, audit_id: str, data: dict) -> None:
    """Persist a full audit record for a user."""
    table = _resource().Table(settings.DYNAMODB_TABLE)
    item = {
        "user_id":          user_id,
        "audit_id":         audit_id,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "brand_name":       data.get("brand_name", ""),
        "niche":            data.get("niche", ""),
        "marketplace":      data.get("marketplace", ""),
        "report_type":      data.get("report_type", ""),
        "audit_purpose":    data.get("audit_purpose", ""),
        "notes":            data.get("notes", ""),
        "brand_analysis":   data.get("brand_analysis", {}),
        "recommendations":  data.get("recommendations", []),
        "benchmark_metrics": data.get("benchmark_metrics", []),
        "csv_metadata":     data.get("csv_metadata", {}),
        "citations":        data.get("citations", []),
    }
    table.put_item(Item=item)


def _to_native(obj):
    """Recursively convert DynamoDB Decimal types to int/float for JSON serialisation."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj


def list_audits(user_id: str) -> list[dict]:
    """Return all audits for a user, sorted newest first."""
    table = _resource().Table(settings.DYNAMODB_TABLE)
    resp = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(user_id),
    )
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return [_to_native(item) for item in items]
