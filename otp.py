"""
OTP UTILITIES FOR NETFLIX BOT
Safe error handling and HTML escaping
"""

import re
import html
from typing import Optional, Dict, Any

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
