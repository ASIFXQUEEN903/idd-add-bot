"""
Clean Telegram OTP Bot - Admin Only
Fixed async issues
"""

import os
import logging
from datetime import datetime
from bson import ObjectId
from pymongo import MongoClient
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Import fixed account manager
from account import AccountManager

# ========================
# CONFIGURATION
# ========================
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
ADMIN_ID = int(os.getenv('ADMIN_ID', 'YOUR_ADMIN_ID_HERE'))

# Pyrogram API credentials
API_ID = int(os.getenv('API_ID', '6435225'))
API_HASH = os.getenv('API_HASH', '4e984ea35f854762dcde906dce426c2d')

# MongoDB
MONGO_URL = os.getenv('MONGO_URL', 'mongodb://localhost:27017/otp_bot')

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

def format_phone(phone):
    """Format phone number for display"""
    return phone if len(phone) <= 15 else f"{phone[:12]}..."

def get_total_accounts():
    """Get total number of accounts"""
    return accounts_col.count_documents({})

def get_all_accounts():
    """Get all accounts"""
    return list(accounts_col.find({}, {"phone": 1, "_id": 1}).sort("created_at", -1))

def save_account(phone, session_string, has_2fa=False, two_step_password=None):
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
                    "updated_at": datetime.utcnow()
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
                "updated_at": datetime.utcnow()
            }
            accounts_col.insert_one(account_data)
            logger.info(f"Account saved: {phone}")
        return True
    except Exception as e:
        logger.error(f"Save account error: {e}")
        return False

def save_otp_log(phone, otp):
    """Save OTP fetch log"""
    try:
        log_data = {
            "phone": phone,
            "otp": otp,
            "fetched_at": datetime.utcnow()
        }
        otp_logs_col.insert_one(log_data)
        logger.info(f"OTP logged for {phone}")
        return True
    except Exception as e:
        logger.error(f"Save OTP log error: {e}")
        return False

# ========================
# BOT HANDLERS
# ========================
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Handle /start command"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.send_message(
            user_id,
            "🚫 Unauthorized\n\nOnly admin can use this bot."
        )
        return
    
    # Clear any existing state
    if user_id in login_states:
        del login_states[user_id]
    
    # Show admin dashboard
    show_admin_dashboard(user_id, message.chat.id)

def show_admin_dashboard(user_id, chat_id=None):
    """Show admin dashboard"""
    if not chat_id:
        chat_id = user_id
    
    total_accounts = get_total_accounts()
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts"),
        InlineKeyboardButton("🔐 Add Account", callback_data="login_account")
    )
    
    bot.send_message(
        chat_id,
        f"👑 **Admin Panel**\n\n📊 Total Accounts: {total_accounts}\n\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "login_account")
def handle_login_account(call):
    """Start login process"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Unauthorized", show_alert=True)
        return
    
    # Set state to ask for phone number
    login_states[user_id] = {"step": "ask_phone"}
    
    bot.edit_message_text(
        "📱 **Enter Phone Number**\n\n"
        "Enter phone number with country code:\n"
        "Example: +919876543210",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
        )
    )

@bot.callback_query_handler(func=lambda call: call.data == "back_to_dashboard")
def handle_back_to_dashboard(call):
    """Go back to dashboard"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    # Clear login state
    if user_id in login_states:
        del login_states[user_id]
    
    show_admin_dashboard(user_id, call.message.chat.id)

@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_phone")
def handle_phone_input(message):
    """Handle phone number input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
    
    phone = message.text.strip()
    
    # Basic phone validation
    if not phone.startswith('+') or len(phone) < 10:
        bot.send_message(
            user_id,
            "❌ Invalid phone format.\n\n"
            "Example: +919876543210\n\n"
            "Enter phone number again:",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        )
        return
    
    # Send OTP using Pyrogram
    bot.send_message(user_id, "⏳ Sending OTP...")
    
    try:
        result = account_manager.send_otp(phone)
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            bot.send_message(
                user_id,
                f"❌ Failed to send OTP:\n{error_msg}\n\n"
                "Enter phone number again:",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
                )
            )
            return
        
        # Store state
        login_states[user_id] = {
            "step": "ask_otp",
            "phone": phone,
            "phone_code_hash": result["phone_code_hash"],
            "session_key": result["session_key"]
        }
        
        bot.send_message(
            user_id,
            f"✅ OTP sent to {phone}\n\n"
            "Enter the 5-digit OTP code:",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
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

@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_otp")
def handle_otp_input(message):
    """Handle OTP input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
    
    otp_code = message.text.strip()
    
    if not otp_code.isdigit() or len(otp_code) != 5:
        bot.send_message(
            user_id,
            "❌ Invalid OTP format. Must be 5 digits.\n\n"
            "Enter OTP code again:",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        )
        return
    
    state = login_states.get(user_id)
    if not state:
        bot.send_message(user_id, "❌ Session expired. Start again with /start")
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
            bot.send_message(
                user_id,
                "🔐 2FA Password Required\n\n"
                "Enter your 2-step verification password:",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
                )
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
        saved = save_account(
            state["phone"],
            result["session_string"],
            result["has_2fa"],
            result.get("two_step_password")
        )
        
        if not saved:
            bot.send_message(
                user_id,
                "❌ Failed to save account to database.\n\n"
                "Start again with /start"
            )
        else:
            bot.send_message(
                user_id,
                f"✅ Account added successfully!\n\n"
                f"📱 Phone: {state['phone']}\n"
                f"🔐 2FA: {'Enabled' if result['has_2fa'] else 'Disabled'}\n\n"
                "Account is now available in your account list."
            )
        
        # Clear state and show dashboard
        if user_id in login_states:
            del login_states[user_id]
        
        show_admin_dashboard(user_id, message.chat.id)
        
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        bot.send_message(
            user_id,
            f"❌ Error verifying OTP:\n{str(e)}\n\n"
            "Start again with /start"
        )
        if user_id in login_states:
            del login_states[user_id]

@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_2fa")
def handle_2fa_input(message):
    """Handle 2FA password input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
    
    password = message.text.strip()
    
    if not password:
        bot.send_message(
            user_id,
            "❌ Password cannot be empty.\n\n"
            "Enter 2FA password again:",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Cancel", callback_data="back_to_dashboard")
            )
        )
        return
    
    state = login_states.get(user_id)
    if not state:
        bot.send_message(user_id, "❌ Session expired. Start again with /start")
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
        saved = save_account(
            state["phone"],
            result["session_string"],
            result["has_2fa"],
            result.get("two_step_password")
        )
        
        if not saved:
            bot.send_message(
                user_id,
                "❌ Failed to save account to database.\n\n"
                "Start again with /start"
            )
        else:
            bot.send_message(
                user_id,
                f"✅ Account added successfully!\n\n"
                f"📱 Phone: {state['phone']}\n"
                f"🔐 2FA: Enabled\n\n"
                "Account is now available in your account list."
            )
        
        # Clear state and show dashboard
        if user_id in login_states:
            del login_states[user_id]
        
        show_admin_dashboard(user_id, message.chat.id)
        
    except Exception as e:
        logger.error(f"2FA verification error: {e}")
        bot.send_message(
            user_id,
            f"❌ Error verifying 2FA:\n{str(e)}\n\n"
            "Start again with /start"
        )
        if user_id in login_states:
            del login_states[user_id]

@bot.callback_query_handler(func=lambda call: call.data == "view_accounts")
def handle_view_accounts(call):
    """Show all accounts"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Unauthorized", show_alert=True)
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
    """Show actions for selected account"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Unauthorized", show_alert=True)
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
    """Fetch latest OTP for account"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Unauthorized", show_alert=True)
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
        save_otp_log(account["phone"], otp)
        
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
    """Handle logout button"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "🚫 Unauthorized", show_alert=True)
        return
    
    account_id = call.data.replace("logout_", "")
    
    # Just go back to account details for now
    # In future, you can implement actual logout logic
    bot.answer_callback_query(call.id, "Logout feature coming soon!", show_alert=True)
    
    # Go back to account details
    call.data = f"account_{account_id}"
    handle_account_selection(call)

@bot.message_handler(func=lambda m: True)
def handle_other_messages(message):
    """Handle all other messages"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.send_message(
            user_id,
            "🚫 Unauthorized\n\nOnly admin can use this bot."
        )
        return
    
    # If admin sends random message, show dashboard
    show_admin_dashboard(user_id, message.chat.id)

# ========================
# RUN BOT
# ========================
if __name__ == "__main__":
    logger.info("🤖 Starting Clean OTP Bot...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"📱 API ID: {API_ID}")
    
    # Create indexes
    try:
        accounts_col.create_index([("phone", 1)], unique=True)
        accounts_col.create_index([("created_at", -1)])
        otp_logs_col.create_index([("fetched_at", -1)])
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Index creation error: {e}")
    
    # Start bot
    logger.info("✅ Bot is running...")
    bot.infinity_polling()
