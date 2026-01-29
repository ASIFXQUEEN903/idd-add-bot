"""
Clean Telegram OTP Bot - Admin Only
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
MONGO_URL = os.getenv('MONGO_URL', 'mongodb+srv://rahul:rahulkr@cluster0.szdpcp6.mongodb.net/?retryWrites=true&w=majority')

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
mongo_client = MongoClient(MONGO_URL)
db = mongo_client.otp_bot
accounts_col = db.accounts
otp_logs_col = db.otp_logs

# Initialize account manager
account_manager = AccountManager(API_ID, API_HASH)

# Store temporary login states
login_states = {}  # {user_id: {step: "phone", phone: "", phone_code_hash: "", client_key: ""}}

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

def save_account(phone, session_string, has_2fa=False, two_step_password=None):
    """Save account to database"""
    account_data = {
        "phone": phone,
        "session_string": session_string,
        "has_2fa": has_2fa,
        "two_step_password": two_step_password,
        "created_at": datetime.utcnow()
    }
    accounts_col.insert_one(account_data)
    logger.info(f"Account saved: {phone}")

def save_otp_log(phone, otp):
    """Save OTP fetch log"""
    log_data = {
        "phone": phone,
        "otp": otp,
        "fetched_at": datetime.utcnow()
    }
    otp_logs_col.insert_one(log_data)
    logger.info(f"OTP logged for {phone}")

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
    
    # Show login button
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔐 Login Account", callback_data="login_account"))
    
    bot.send_message(
        user_id,
        "Please login to continue",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "login_account")
def handle_login_account(call):
    """Start login process"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    # Set state to ask for phone number
    login_states[user_id] = {"step": "ask_phone"}
    
    bot.edit_message_text(
        "📱 Enter phone number with country code:\n\nExample: +919876543210",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=None
    )

@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_phone")
def handle_phone_input(message):
    """Handle phone number input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
    
    phone = message.text.strip()
    
    # Basic phone validation
    if not phone.startswith('+') or len(phone) < 10:
        bot.send_message(user_id, "❌ Invalid phone format. Example: +919876543210\n\nEnter phone number:")
        return
    
    # Send OTP using Pyrogram
    bot.send_message(user_id, "⏳ Sending OTP...")
    
    result = account_manager.run_async(
        account_manager.send_otp(phone)
    )
    
    if not result["success"]:
        bot.send_message(
            user_id,
            f"❌ Failed to send OTP: {result.get('error', 'Unknown error')}\n\nEnter phone number again:"
        )
        return
    
    # Store state
    login_states[user_id] = {
        "step": "ask_otp",
        "phone": phone,
        "phone_code_hash": result["phone_code_hash"],
        "client_key": result["client_key"]
    }
    
    bot.send_message(user_id, "✅ OTP sent! Enter the 5-digit code:")

@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_otp")
def handle_otp_input(message):
    """Handle OTP input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
    
    otp_code = message.text.strip()
    
    if not otp_code.isdigit() or len(otp_code) != 5:
        bot.send_message(user_id, "❌ Invalid OTP format. Enter 5-digit code:")
        return
    
    state = login_states[user_id]
    
    bot.send_message(user_id, "⏳ Verifying OTP...")
    
    result = account_manager.run_async(
        account_manager.verify_otp(
            state["client_key"],
            otp_code,
            state["phone"],
            state["phone_code_hash"]
        )
    )
    
    if result.get("needs_2fa"):
        # 2FA required
        login_states[user_id]["step"] = "ask_2fa"
        bot.send_message(user_id, "🔐 2FA required. Enter your password:")
        return
    
    if not result["success"]:
        bot.send_message(
            user_id,
            f"❌ OTP verification failed: {result.get('error', 'Unknown error')}\n\nStart again with /start"
        )
        if user_id in login_states:
            del login_states[user_id]
        return
    
    # Save account to database
    save_account(
        state["phone"],
        result["session_string"],
        result["has_2fa"],
        result.get("two_step_password")
    )
    
    # Clear state
    del login_states[user_id]
    
    # Show admin dashboard
    show_admin_dashboard(user_id, message.chat.id)

@bot.message_handler(func=lambda m: login_states.get(m.from_user.id, {}).get("step") == "ask_2fa")
def handle_2fa_input(message):
    """Handle 2FA password input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
    
    password = message.text.strip()
    
    if not password:
        bot.send_message(user_id, "❌ Password cannot be empty. Enter password:")
        return
    
    state = login_states[user_id]
    
    bot.send_message(user_id, "⏳ Verifying 2FA password...")
    
    result = account_manager.run_async(
        account_manager.verify_2fa(state["client_key"], password)
    )
    
    if not result["success"]:
        bot.send_message(
            user_id,
            f"❌ 2FA verification failed: {result.get('error', 'Unknown error')}\n\nStart again with /start"
        )
        if user_id in login_states:
            del login_states[user_id]
        return
    
    # Save account to database
    save_account(
        state["phone"],
        result["session_string"],
        result["has_2fa"],
        result.get("two_step_password")
    )
    
    # Clear state
    del login_states[user_id]
    
    # Show admin dashboard
    show_admin_dashboard(user_id, message.chat.id)

def show_admin_dashboard(user_id, chat_id=None):
    """Show admin dashboard"""
    if not chat_id:
        chat_id = user_id
    
    total_accounts = get_total_accounts()
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts"))
    markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="login_account"))
    
    bot.send_message(
        chat_id,
        f"👑 Admin Panel\n\nTotal Accounts: {total_accounts}",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "view_accounts")
def handle_view_accounts(call):
    """Show all accounts"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    # Fetch all accounts
    accounts = list(accounts_col.find({}, {"phone": 1, "_id": 1}))
    
    if not accounts:
        bot.edit_message_text(
            "📱 No accounts found\n\nAdd an account first.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔐 Add Account", callback_data="login_account"),
                InlineKeyboardButton("⬅️ Back", callback_data="back_to_dashboard")
            )
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
    
    markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_dashboard"))
    
    bot.edit_message_text(
        "📱 Select Account:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("account_"))
def handle_account_selection(call):
    """Show actions for selected account"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    account_id = call.data.replace("account_", "")
    
    try:
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            return
        
        # Show account actions
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔢 Get Latest OTP", callback_data=f"get_otp_{account_id}"))
        markup.add(InlineKeyboardButton("❌ Cancel", callback_data="view_accounts"))
        
        phone_display = format_phone(account["phone"])
        has_2fa = "✅" if account.get("has_2fa") else "❌"
        
        bot.edit_message_text(
            f"📱 Account: {phone_display}\n"
            f"🔐 2FA: {has_2fa}\n\n"
            f"Select action:",
            call.message.chat.id,
            call.message.message_id,
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
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    account_id = call.data.replace("get_otp_", "")
    
    try:
        account = accounts_col.find_one({"_id": ObjectId(account_id)})
        if not account:
            bot.answer_callback_query(call.id, "Account not found", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "⏳ Fetching OTP...")
        
        # Fetch OTP using Pyrogram
        otp = account_manager.run_async(
            account_manager.get_latest_otp(
                account["session_string"],
                account["phone"]
            )
        )
        
        if not otp:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Try Again", callback_data=f"get_otp_{account_id}"))
            markup.add(InlineKeyboardButton("❌ Cancel", callback_data=f"account_{account_id}"))
            
            bot.edit_message_text(
                f"📱 Phone: {format_phone(account['phone'])}\n"
                f"🔢 OTP: Not found\n\n"
                f"No OTP found in recent messages.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            return
        
        # Save OTP to logs
        save_otp_log(account["phone"], otp)
        
        # Prepare message
        message = f"📱 Phone: {format_phone(account['phone'])}\n"
        message += f"🔢 OTP: {otp}\n"
        
        if account.get("has_2fa") and account.get("two_step_password"):
            message += f"🔐 2FA: {account['two_step_password']}\n"
        
        message += f"\n✅ OTP fetched at: {datetime.utcnow().strftime('%H:%M:%S')}"
        
        # Create buttons
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Get OTP Again", callback_data=f"get_otp_{account_id}"))
        markup.add(InlineKeyboardButton("❌ Cancel", callback_data=f"account_{account_id}"))
        
        bot.edit_message_text(
            message,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Get OTP error: {e}")
        bot.answer_callback_query(call.id, "Error fetching OTP", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_dashboard")
def handle_back_to_dashboard(call):
    """Go back to dashboard"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    total_accounts = get_total_accounts()
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts"))
    markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="login_account"))
    
    bot.edit_message_text(
        f"👑 Admin Panel\n\nTotal Accounts: {total_accounts}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "cancel_process")
def handle_cancel_process(call):
    """Cancel current process"""
    user_id = call.from_user.id
    
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return
    
    # Clear any login state
    if user_id in login_states:
        del login_states[user_id]
    
    show_admin_dashboard(user_id, call.message.chat.id)

# ========================
# MESSAGE HANDLER FOR NON-ADMINS
# ========================
@bot.message_handler(func=lambda m: True)
def handle_other_messages(message):
    """Handle all other messages from non-admins"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.send_message(
            user_id,
            "🚫 Unauthorized\n\nOnly admin can use this bot."
        )
        return
    
    # If admin sends random message, show dashboard
    if user_id == ADMIN_ID:
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
        otp_logs_col.create_index([("fetched_at", -1)])
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Index creation error: {e}")
    
    # Start bot
    bot.infinity_polling()
