import os
import uuid
import hashlib
from datetime import datetime
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key
from dotenv import load_dotenv

def _get_table(name: str):
    load_dotenv(override=True)
    db = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION_NAME', 'ap-south-1'))
    return db.Table(name)

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username: str, password: str, full_name: str = "") -> dict:
    table = _get_table('HA_Users')
    try:
        resp = table.get_item(Key={'username': username.strip().lower()})
        if 'Item' in resp:
            return {"status": "error", "message": "Username already exists. Please choose another."}
        table.put_item(Item={
            'username': username.strip().lower(),
            'password_hash': _hash_password(password),
            'full_name': full_name.strip(),
            'created_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        return {"status": "success", "username": username.strip().lower()}
    except Exception as e:
        return {"status": "error", "message": f"Error registering user: {e}"}

def login_user(username: str, password: str) -> dict:
    table = _get_table('HA_Users')
    try:
        resp = table.get_item(Key={'username': username.strip().lower()})
        if 'Item' in resp:
            item = resp['Item']
            if item.get('password_hash') == _hash_password(password):
                return {"status": "success", "username": item.get('username'), "full_name": item.get('full_name', item.get('username'))}
    except Exception:
        pass
    return {"status": "error", "message": "Invalid username or password."}

import json

def store_health_parameter(user_id: str, parameter_name: str = "", parameter_value: float = 0.0, unit: str = "", parameters_json: str = "") -> dict:
    table = _get_table('HA_Params')
    timestamp = datetime.now().isoformat()
    
    items = []
    if parameters_json:
        try:
            items = json.loads(parameters_json)
        except Exception:
            pass
            
    if not items:
        items = [{"name": parameter_name, "value": parameter_value, "unit": unit}]
    
    stored = 0
    with table.batch_writer() as batch:
        for item in items:
            p_name = item.get("name", "")
            if not p_name: continue
            try:
                safe_val = Decimal(str(float(item.get("value", 0.0))))
            except (ValueError, TypeError, Exception):
                safe_val = Decimal("0.0")

            batch.put_item(Item={
                'user_id': user_id,
                'timestamp_id': f"{timestamp}#{uuid.uuid4().hex[:8]}",
                'timestamp': timestamp,
                'parameter_name': p_name.lower(),
                'parameter_value': safe_val,
                'unit': str(item.get("unit", ""))
            })
            stored += 1
            
    return {
        "status": "success",
        "action": "store_health_parameter",
        "message": f"Successfully stored {stored} parameter(s) for user {user_id}"
    }

def get_health_parameters(user_id: str, parameter_name: str) -> dict:
    table = _get_table('HA_Params')
    resp = table.query(KeyConditionExpression=Key('user_id').eq(user_id))
    items = resp.get('Items', [])
    items.sort(key=lambda x: x['timestamp'], reverse=True)
    if parameter_name and parameter_name.lower() != 'all':
        items = [i for i in items if i['parameter_name'] == parameter_name.lower()]
    
    results = [
        {"timestamp": i['timestamp'], "parameter": i['parameter_name'], "value": float(i['parameter_value']), "unit": i['unit']}
        for i in items
    ]
    return {
        "status": "success",
        "action": "get_health_parameters",
        "data": results,
    }

def set_reminder(user_id: str, reminder_message: str, trigger_time: str) -> dict:
    import dateparser
    dt = dateparser.parse(trigger_time, settings={'PREFER_DATES_FROM': 'future'})
    if not dt:
        dt = datetime.now()
    trigger_iso = dt.strftime("%Y-%m-%d %H:%M:%S")
    
    table = _get_table('HA_Reminders')
    rem_id = str(uuid.uuid4())
    table.put_item(Item={
        'user_id': user_id,
        'id': rem_id,
        'reminder_message': reminder_message,
        'trigger_time': trigger_iso,
        'is_completed': 0
    })
    return {
        "status": "success",
        "action": "set_reminder",
        "message": f"Reminder set: '{reminder_message}' at {trigger_iso}"
    }

def fetch_due_reminders(user_id: str) -> dict | None:
    table = _get_table('HA_Reminders')
    resp = table.query(KeyConditionExpression=Key('user_id').eq(user_id))
    items = resp.get('Items', [])
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Sort to get the oldest due reminder first
    sorted_items = sorted(items, key=lambda x: x.get('trigger_time', ''))
    
    for item in sorted_items:
        if item.get('is_completed') == 0 and item.get('trigger_time', '') <= now_iso:
            return {"id": item['id'], "message": item['reminder_message'], "time": item['trigger_time']}
    return None

def mark_reminder_completed(reminder_id) -> bool:
    table = _get_table('HA_Reminders')
    resp = table.scan(FilterExpression=Key('id').eq(str(reminder_id)))
    items = resp.get('Items', [])
    if items:
        uid = items[0]['user_id']
        table.update_item(
            Key={'user_id': uid, 'id': str(reminder_id)},
            UpdateExpression='SET is_completed = :val',
            ExpressionAttributeValues={':val': 1}
        )
        return True
    return False

def submit_doctor_feedback(user_id: str, doctor_id: str, rating: float, feedback_text: str, visit_date: str) -> dict:
    table = _get_table('HA_Feedback')
    table.put_item(Item={
        'doctor_id': doctor_id,
        'id': str(uuid.uuid4()),
        'user_id': user_id,
        'rating': Decimal(str(rating)),
        'feedback_text': feedback_text,
        'visit_date': visit_date,
        'submitted_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    return {
        "status": "success",
        "action": "submit_doctor_feedback",
        "message": f"Feedback submitted for doctor {doctor_id}"
    }

def _get_doctor_ratings() -> dict:
    table = _get_table('HA_Feedback')
    resp = table.scan()
    items = resp.get('Items', [])
    from collections import defaultdict
    counts = defaultdict(list)
    for i in items:
        counts[i['doctor_id']].append(float(i['rating']))
    doctors = {did: sum(rats)/len(rats) for did, rats in counts.items()}
    return doctors

def get_health_summary_data(user_id: str) -> dict:
    table = _get_table('HA_Params')
    resp = table.query(KeyConditionExpression=Key('user_id').eq(user_id))
    items = resp.get('Items', [])
    items.sort(key=lambda x: x['timestamp'], reverse=True)
    
    def _recency(days: float) -> str:
        if days < 1: return "today"
        if days < 2: return "yesterday"
        if days < 7: return f"{int(days)} days ago"
        if days < 30: return f"{int(days // 7)} week(s) ago"
        if days < 365: return f"{int(days // 30)} month(s) ago"
        return f"{int(days // 365)} year(s) ago"

    params = []
    now = datetime.now()
    from dateutil.parser import parse
    for i in items:
        ts_str = i.get('timestamp')
        try:
            diff = (now.timestamp() - parse(ts_str).timestamp()) / 86400.0
        except Exception:
            diff = 0
            
        params.append({
            "parameter_name": i['parameter_name'],
            "value": float(i['parameter_value']),
            "unit": i['unit'],
            "timestamp": ts_str,
            "days_ago": round(abs(diff), 1),
            "recency": _recency(abs(diff))
        })
        
    trends = {}
    from collections import defaultdict
    grouped = defaultdict(list)
    for p in params: grouped[p["parameter_name"]].append(p)
    for p_name, plist in grouped.items():
        if len(plist) > 1:
            first = float(plist[-1]["value"]) # Since chronological
            last = float(plist[0]["value"])
            diff = last - first
            if diff > 0: trends[p_name] = f"Increased by {diff:.1f}"
            elif diff < 0: trends[p_name] = f"Decreased by {abs(diff):.1f}"
            else: trends[p_name] = "Stable"
    
    return {
        "status": "success",
        "action": "get_health_summary_data",
        "total_readings": len(params),
        "data": params,
        "parameter_trends": trends
    }

def _load_doctors() -> list:
    table = _get_table('HA_Doctors')
    resp = table.scan()
    items = resp.get('Items', [])
    
    def replace_decimals(obj):
        from decimal import Decimal
        if isinstance(obj, list):
            return [replace_decimals(i) for i in obj]
        elif isinstance(obj, dict):
            return {k: replace_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return obj

    return [replace_decimals(doc) for doc in items]
