"""
OTP UTILITIES FOR NETFLIX BOT
With account management and pagination
"""

import re
import html
import math
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from bson import ObjectId

def safe_error_message(error: Exception) -> str:
    """Convert exception to safe user-friendly message"""
    error_msg = str(error)
    
    # Escape HTML
    safe_msg = html.escape(error_msg)
    
    # Truncate if too long
    if len(safe_msg) > 200:
        safe_msg = safe_msg[:197] + "..."
    
    # Map common errors to friendly messages
    error_mapping = {
        "SESSION_EXPIRED": "Session expired. Please start again with /start",
        "INVALID_PHONE": "Invalid phone number format. Please use +CountryCodeNumber",
        "FLOOD_WAIT": "Too many attempts. Please wait and try again",
        "PHONECODEINVALID": "Invalid verification code. Please check and try again",
        "PHONECODEEXPIRED": "Verification code expired. Please request new code",
        "SESSIONPASSWORDNEEDED": "This account has 2-step verification. Please enter your password",
        "INVALID_2FA": "Incorrect 2FA password. Please try again",
    }
    
    for key, message in error_mapping.items():
        if key in error_msg.upper():
            return message
    
    return f"Error: {safe_msg}"

def validate_phone(phone: str) -> tuple[bool, str]:
    """Validate phone number format"""
    phone = phone.strip()
    
    if not phone.startswith('+'):
        return False, "Phone number must start with +"
    
    if len(phone) < 10 or len(phone) > 16:
        return False, "Phone number must be 10-16 digits"
    
    # Check if it contains only digits after +
    if not phone[1:].replace(" ", "").isdigit():
        return False, "Phone number can only contain digits"
    
    return True, phone

def validate_otp(otp: str) -> bool:
    """Validate OTP format"""
    return otp.isdigit() and len(otp) in [5, 6]

def extract_otp_from_text(text: str) -> Optional[str]:
    """Extract OTP from text message"""
    if not text:
        return None
    
    # Look for 5-digit OTP
    matches = re.findall(r'\b\d{5}\b', text)
    if matches:
        return matches[0]
    
    # Look for 6-digit OTP
    matches = re.findall(r'\b\d{6}\b', text)
    if matches:
        return matches[0]
    
    return None

def format_phone_display(phone: str) -> str:
    """Format phone number for safe display"""
    if len(phone) <= 8:
        return phone
    
    return f"{phone[:4]}****{phone[-4:]}"

def create_safe_response(success: bool, message: str, 
                        data: Optional[Dict] = None,
                        error_code: Optional[str] = None) -> Dict[str, Any]:
    """Create safe response dictionary"""
    response = {
        "success": success,
        "message": html.escape(message) if message else "",
        "data": data or {}
    }
    
    if error_code:
        response["error_code"] = error_code
    
    return response

def handle_pyrogram_error(error: Exception) -> Dict[str, Any]:
    """Handle Pyrogram errors safely"""
    error_name = type(error).__name__
    
    # Common error mappings
    if error_name == "PhoneNumberInvalid":
        return create_safe_response(
            False,
            "Invalid phone number format. Please use +CountryCodeNumber",
            error_code="INVALID_PHONE"
        )
    elif error_name == "PhoneCodeInvalid":
        return create_safe_response(
            False,
            "Invalid verification code. Please check and try again",
            error_code="INVALID_CODE"
        )
    elif error_name == "PhoneCodeExpired":
        return create_safe_response(
            False,
            "Verification code expired. Please request new code",
            error_code="CODE_EXPIRED"
        )
    elif error_name == "SessionPasswordNeeded":
        return create_safe_response(
            False,
            "This account has 2-step verification. Please enter your password",
            error_code="NEEDS_2FA"
        )
    elif error_name == "FloodWait":
        wait_time = getattr(error, 'value', 60)
        return create_safe_response(
            False,
            f"Too many attempts. Please wait {wait_time} seconds",
            error_code="FLOOD_WAIT",
            data={"wait_time": wait_time}
        )
    else:
        safe_msg = html.escape(str(error)[:100])
        return create_safe_response(
            False,
            f"Error: {safe_msg}",
            error_code="UNKNOWN_ERROR"
        )

def escape_html(text: str) -> str:
    """Escape HTML special characters"""
    return html.escape(text)

def create_plain_text_message(original_text: str) -> str:
    """Convert HTML message to plain text for safe sending"""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', original_text)
    
    # Escape any remaining HTML
    text = html.escape(text)
    
    # Ensure it's not empty
    if not text.strip():
        text = "Please try again with /start"
    
    return text

def get_paginated_accounts(accounts_col, page: int = 1, per_page: int = 5) -> Tuple[List[Dict], int, int]:
    """Get paginated accounts with total pages"""
    try:
        # Get total count
        total_accounts = accounts_col.count_documents({})
        total_pages = math.ceil(total_accounts / per_page)
        
        # Validate page
        if page < 1:
            page = 1
        elif page > total_pages and total_pages > 0:
            page = total_pages
        
        # Calculate skip
        skip = (page - 1) * per_page
        
        # Get accounts for current page
        accounts = list(accounts_col.find(
            {},
            {"phone": 1, "_id": 1, "created_at": 1, "status": 1, "has_2fa": 1}
        ).sort("created_at", -1).skip(skip).limit(per_page))
        
        return accounts, total_pages, total_accounts
        
    except Exception as e:
        print(f"Pagination error: {e}")
        return [], 0, 0

def format_accounts_list(accounts: List[Dict], page: int, total_pages: int, total_accounts: int) -> str:
    """Format accounts list for display"""
    if not accounts:
        return "📱 No accounts found"
    
    text = f"<b>📱 All Accounts (Page {page}/{total_pages})</b>\n\n"
    
    start_num = (page - 1) * 5 + 1
    for idx, account in enumerate(accounts, start=start_num):
        phone_display = format_phone_display(account.get("phone", "N/A"))
        status_icon = "✅" if account.get("status") == "active" else "⚠️"
        has_2fa = "🔐" if account.get("has_2fa") else ""
        
        # Shorten ID for display
        acc_id = str(account.get("_id", ""))[:8]
        
        text += f"{idx}. {status_icon}{has_2fa} <code>{phone_display}</code>\n"
        text += f"   <i>ID: {acc_id}...</i>\n\n"
    
    text += f"<i>Total Accounts: {total_accounts}</i>"
    
    return text

def create_accounts_keyboard(accounts: List[Dict], page: int, total_pages: int) -> Any:
    """Create keyboard for accounts list"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    markup = InlineKeyboardMarkup(row_width=2)
    
    # Add account buttons (5 per page)
    buttons = []
    for account in accounts:
        phone = account.get("phone", "N/A")
        phone_display = format_phone_display(phone)
        short_phone = phone_display[:12] if len(phone_display) > 12 else phone_display
        account_id = str(account.get("_id", ""))
        
        btn_text = f"📱 {short_phone}"
        buttons.append(InlineKeyboardButton(btn_text, callback_data=f"viewacc_{account_id}"))
    
    # Add buttons in rows of 2
    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            markup.add(buttons[i], buttons[i + 1])
        else:
            markup.add(buttons[i])
    
    # Add pagination buttons
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    
    if page < total_pages:
        pagination_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    
    if pagination_buttons:
        markup.add(*pagination_buttons)
    
    # Add action buttons
    action_buttons = []
    if len(accounts) == 5 and total_pages > 1:
        action_buttons.append(InlineKeyboardButton(f"📄 Page {page}/{total_pages}", callback_data="current_page"))
    
    action_buttons.append(InlineKeyboardButton("➕ Add Account", callback_data="admin_login"))
    action_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
    
    if len(action_buttons) > 2:
        markup.add(action_buttons[0])
        markup.add(action_buttons[1], action_buttons[2])
    else:
        markup.add(*action_buttons)
    
    return markup

def create_account_detail_keyboard(account_id: str) -> Any:
    """Create keyboard for account details"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    markup = InlineKeyboardMarkup(row_width=2)
    
    # Action buttons
    markup.add(
        InlineKeyboardButton("🔢 Get Latest OTP", callback_data=f"getotp_{account_id}"),
        InlineKeyboardButton("🗑️ Remove Account", callback_data=f"remove_{account_id}")
    )
    
    # Navigation buttons
    markup.add(
        InlineKeyboardButton("📱 All Accounts", callback_data="view_accounts"),
        InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin")
    )
    
    return markup

def format_account_details(account: Dict) -> str:
    """Format account details for display"""
    phone = account.get("phone", "N/A")
    phone_display = format_phone_display(phone)
    
    has_2fa = account.get("has_2fa", False)
    status = account.get("status", "active")
    created_at = account.get("created_at", datetime.utcnow())
    updated_at = account.get("updated_at", datetime.utcnow())
    account_id = str(account.get("_id", ""))[:12]
    
    # Format dates
    created_str = created_at.strftime('%d %b %Y %H:%M') if isinstance(created_at, datetime) else "N/A"
    updated_str = updated_at.strftime('%d %b %Y %H:%M') if isinstance(updated_at, datetime) else "N/A"
    
    text = f"""
<b>📱 Account Details</b>

<b>Phone:</b> <code>{phone_display}</code>
<b>Account ID:</b> <code>{account_id}</code>
<b>Status:</b> {"✅ Active" if status == "active" else "⚠️ Inactive"}
<b>2FA:</b> {"✅ Enabled" if has_2fa else "❌ Disabled"}
<b>Created:</b> {created_str}
<b>Updated:</b> {updated_str}

<b>Actions:</b>
• Get Latest OTP
• Remove Account (Logout)
"""
    
    return text

def format_otp_result(phone: str, otp: str, account: Dict = None) -> str:
    """Format OTP result for display"""
    phone_display = format_phone_display(phone)
    current_time = datetime.utcnow().strftime('%H:%M:%S')
    
    text = f"""
<b>✅ OTP Fetched Successfully</b>

<b>Phone:</b> <code>{phone_display}</code>
<b>OTP:</b> <code>{otp}</code>
<b>Time:</b> {current_time}
"""
    
    if account and account.get("has_2fa") and account.get("two_step_password"):
        password = account.get("two_step_password", "N/A")
        text += f"<b>2FA Password:</b> <code>{password}</code>\n"
    
    text += f"\n<i>OTP will expire in 5 minutes</i>"
    
    return text
