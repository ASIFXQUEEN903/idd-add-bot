"""
PROFESSIONAL PYROGRAM ACCOUNT MANAGER v2.0
Fixed Async Loop Management for Multiple Concurrent Users
"""

import os
import re
import asyncio
import logging
import threading
import time
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from cryptography.fernet import Fernet
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, FloodWait, PhoneCodeEmpty, AuthKeyUnregistered,
    UserDeactivatedBan, UserDeactivated, SessionRevoked, AuthKeyDuplicated
)

# ========================
# CONFIGURATION
# ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========================
# ENCRYPTION MANAGER
# ========================
class EncryptionManager:
    """Handle encryption/decryption of sensitive data"""
    
    def __init__(self, encryption_key: Optional[str] = None):
        if encryption_key:
            self.key = encryption_key.encode()
        else:
            # Generate key if not provided (store this securely!)
            self.key = Fernet.generate_key()
        
        self.cipher = Fernet(self.key)
    
    def encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return data  # Fallback to plaintext
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception:
            # If decryption fails, assume it's plaintext
            return encrypted_data
    
    def get_key(self) -> str:
        """Get encryption key"""
        return self.key.decode()

# ========================
# RATE LIMITER
# ========================
class RateLimiter:
    """Prevent abuse and rate limiting"""
    
    def __init__(self):
        self.phone_attempts: Dict[str, List[float]] = {}
        self.ip_attempts: Dict[str, List[float]] = {}
        self.session_attempts: Dict[str, List[float]] = {}
        self.lock = threading.Lock()
    
    def check_phone_attempt(self, phone_number: str, max_attempts: int = 3, window: int = 3600) -> Tuple[bool, Optional[int]]:
        """Check if phone number has exceeded attempt limits"""
        with self.lock:
            key = f"phone_{phone_number}"
            current_time = time.time()
            
            # Clean old attempts
            if key in self.phone_attempts:
                self.phone_attempts[key] = [
                    t for t in self.phone_attempts[key]
                    if current_time - t < window
                ]
            
            # Check attempts
            attempts = self.phone_attempts.get(key, [])
            if len(attempts) >= max_attempts:
                wait_time = int(window - (current_time - attempts[0]))
                return False, wait_time
            
            # Record attempt
            if key not in self.phone_attempts:
                self.phone_attempts[key] = []
            self.phone_attempts[key].append(current_time)
            return True, None
    
    def check_ip_attempt(self, ip_address: str, max_attempts: int = 10, window: int = 300) -> bool:
        """Check IP-based rate limiting"""
        with self.lock:
            current_time = time.time()
            
            # Clean old attempts
            if ip_address in self.ip_attempts:
                self.ip_attempts[ip_address] = [
                    t for t in self.ip_attempts[ip_address]
                    if current_time - t < window
                ]
            
            # Check attempts
            attempts = self.ip_attempts.get(ip_address, [])
            if len(attempts) >= max_attempts:
                return False
            
            # Record attempt
            if ip_address not in self.ip_attempts:
                self.ip_attempts[ip_address] = []
            self.ip_attempts[ip_address].append(current_time)
            return True
    
    def reset_attempts(self, phone_number: str = None, ip_address: str = None):
        """Reset attempts for specific key"""
        with self.lock:
            if phone_number:
                key = f"phone_{phone_number}"
                self.phone_attempts.pop(key, None)
            if ip_address:
                self.ip_attempts.pop(ip_address, None)

# ========================
# SESSION VALIDATOR
# ========================
class SessionValidator:
    """Validate session strings and check their status"""
    
    @staticmethod
    def is_valid_session_format(session_string: str) -> bool:
        """Check if session string has valid format"""
        if not session_string or len(session_string) < 100:
            return False
        
        # Basic format check (Telegram session strings have specific patterns)
        try:
            # Check if it's base64 encoded (common for Pyrogram sessions)
            import base64
            # Try to decode as base64
            base64.b64decode(session_string + '==')
            return True
        except:
            # Might be hex or other format
            return bool(re.match(r'^[a-fA-F0-9]+$', session_string)) or ':' in session_string
    
    @staticmethod
    def check_session_age(session_string: str) -> Optional[int]:
        """Estimate session age (in days) based on patterns"""
        # This is a heuristic - actual age checking requires API call
        try:
            if len(session_string) > 200:
                # Newer sessions tend to be longer
                return 0
            return None
        except:
            return None

# ========================
# DEVICE MANAGER
# ========================
class DeviceManager:
    """Manage device information for sessions"""
    
    DEVICE_PROFILES = [
        # iOS Devices
        {
            "device_model": "Netflix 9",
            "system_version": "iOS 17.2",
            "app_version": "Telegram iOS 10.5.1",
            "lang_code": "en"
        },
        {
            "device_model": "Netflix 10",
            "system_version": "iOS 16.6",
            "app_version": "Telegram iOS 9.8.2",
            "lang_code": "en"
        },
        # Android Devices
        {
            "device_model": "Netflix 11",
            "system_version": "Android 14",
            "app_version": "Telegram Android 10.8.0",
            "lang_code": "en"
        },
        {
            "device_model": "Netflix 03",
            "system_version": "Android 14",
            "app_version": "Telegram Android 10.2.3",
            "lang_code": "en"
        },
        # Desktop
        {
            "device_model": "Netflix org",
            "system_version": "Windows 11",
            "app_version": "Telegram Desktop 4.9.1",
            "lang_code": "en"
        },
        {
            "device_model": "Netflix 12",
            "system_version": "macOS 14.2",
            "app_version": "Telegram Desktop 4.8.5",
            "lang_code": "en"
        }
    ]
    
    @staticmethod
    def get_random_device() -> Dict[str, str]:
        """Get random device configuration"""
        import random
        return random.choice(DeviceManager.DEVICE_PROFILES)
    
    @staticmethod
    def get_device_by_type(device_type: str = "android") -> Optional[Dict[str, str]]:
        """Get device configuration by type"""
        for device in DeviceManager.DEVICE_PROFILES:
            if device_type == "ios" and "iPhone" in device["device_model"]:
                return device
            elif device_type == "android" and "Android" in device["system_version"]:
                return device
            elif device_type == "desktop" and device["device_model"] == "Desktop":
                return device
        return DeviceManager.get_random_device()

# ========================
# ASYNC MANAGER (FIXED FOR MULTIPLE USERS)
# ========================
class AsyncManager:
    """Thread-safe async manager for multiple concurrent users"""
    
    _instance = None
    _loop = None
    _lock = threading.Lock()
    _thread_loops = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AsyncManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            self.thread_local = threading.local()
    
    def get_event_loop_for_thread(self):
        """Get or create event loop for current thread"""
        thread_id = threading.get_ident()
        
        if thread_id not in self._thread_loops or self._thread_loops[thread_id].is_closed():
            with self._lock:
                if thread_id not in self._thread_loops or self._thread_loops[thread_id].is_closed():
                    # Create new loop for this thread
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    self._thread_loops[thread_id] = new_loop
        
        return self._thread_loops[thread_id]
    
    def run_async(self, coro):
        """Run async coroutine in sync context safely"""
        loop = self.get_event_loop_for_thread()
        
        try:
            # Check if loop is running
            if loop.is_running():
                # If loop is running, we need to run in separate thread
                return self._run_in_separate_thread(coro)
            else:
                # Run in current loop
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async task: {e}")
            # Fallback: create new loop
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
    
    def _run_in_separate_thread(self, coro):
        """Run coroutine in separate thread with its own event loop"""
        result = None
        exception = None
        event = threading.Event()
        
        def run():
            nonlocal result, exception
            try:
                # Create new event loop for this thread
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                
                # Run the coroutine
                result = new_loop.run_until_complete(coro)
                new_loop.close()
            except Exception as e:
                exception = e
            finally:
                event.set()
        
        # Start thread and wait for completion
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        event.wait()
        
        if exception:
            raise exception
        return result
    
    async def run_with_timeout(self, coro, timeout: int = 30):
        """Run coroutine with timeout"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Operation timed out after {timeout} seconds")
            raise TimeoutError(f"Operation timed out after {timeout} seconds")

# ========================
# ACCOUNT MANAGER (MAIN CLASS)
# ========================
class ProfessionalAccountManager:
    """
    Professional Account Manager with enhanced features:
    - Rate limiting
    - Session validation
    - Device spoofing
    - Error recovery
    - Detailed logging
    - Thread-safe async operations
    """
    
    def __init__(self, api_id: int, api_hash: str, encryption_key: Optional[str] = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.async_manager = AsyncManager()
        self.rate_limiter = RateLimiter()
        self.encryption = EncryptionManager(encryption_key)
        self.validator = SessionValidator()
        self.device_manager = DeviceManager()
        
        # Store active sessions with thread safety
        self.login_sessions: Dict[str, Dict] = {}
        self.active_clients: Dict[str, Client] = {}
        self.sessions_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            "logins_attempted": 0,
            "logins_successful": 0,
            "logins_failed": 0,
            "otps_fetched": 0,
            "errors": {},
            "start_time": time.time()
        }
        
        # Auto-cleanup thread
        self.cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
        self.cleanup_thread.start()
        
        logger.info(f"Account Manager initialized for API ID: {api_id}")
    
    # ========================
    # PUBLIC METHODS
    # ========================
    
    def send_otp(self, phone_number: str, device_type: str = "random", 
                 ip_address: str = "unknown") -> Dict[str, Any]:
        """Send OTP to phone number with rate limiting"""
        
        # Update stats
        self.stats["logins_attempted"] += 1
        
        # Check rate limits
        phone_allowed, wait_time = self.rate_limiter.check_phone_attempt(phone_number)
        if not phone_allowed:
            self.stats["logins_failed"] += 1
            return {
                "success": False,
                "error": f"Too many attempts. Please wait {wait_time} seconds.",
                "error_code": "RATE_LIMITED",
                "wait_time": wait_time
            }
        
        if not self.rate_limiter.check_ip_attempt(ip_address):
            return {
                "success": False,
                "error": "Too many requests from this IP. Please try again later.",
                "error_code": "IP_RATE_LIMITED"
            }
        
        # Run async operation
        return self.async_manager.run_async(
            self._send_otp_async(phone_number, device_type)
        )
    
    def verify_otp(self, session_key: str, otp_code: str, 
                   phone_number: str, phone_code_hash: str) -> Dict[str, Any]:
        """Verify OTP and get session string"""
        return self.async_manager.run_async(
            self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash)
        )
    
    def verify_2fa(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA password"""
        return self.async_manager.run_async(
            self._verify_2fa_async(session_key, password)
        )
    
    def get_latest_otp(self, session_string: str, phone: str, 
                       max_messages: int = 50) -> Optional[str]:
        """Fetch latest OTP from messages"""
        result = self.async_manager.run_async(
            self._get_latest_otp_async(session_string, phone, max_messages)
        )
        if result:
            self.stats["otps_fetched"] += 1
        return result
    
    def validate_session(self, session_string: str, phone: str = None) -> Dict[str, Any]:
        """Validate session string and check if it's working"""
        return self.async_manager.run_async(
            self._validate_session_async(session_string, phone)
        )
    
    def get_account_info(self, session_string: str) -> Dict[str, Any]:
        """Get account information from session"""
        return self.async_manager.run_async(
            self._get_account_info_async(session_string)
        )
    
    # ========================
    # ASYNC IMPLEMENTATIONS (THREAD-SAFE)
    # ========================
    
    async def _send_otp_async(self, phone_number: str, device_type: str) -> Dict[str, Any]:
        """Async implementation of send_otp"""
        client = None
        session_key = None
        
        try:
            # Get device configuration
            device = self.device_manager.get_device_by_type(device_type)
            
            # Create unique session name
            timestamp = int(time.time())
            session_name = f"login_{phone_number}_{timestamp}"
            
            # Create client with device info
            client = Client(
                name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                lang_code=device["lang_code"],
                in_memory=True,
                no_updates=True
            )
            
            # Connect and send code
            await client.connect()
            
            # Check if already authorized
            try:
                if await client.get_me():
                    await self._safe_disconnect(client)
                    return {
                        "success": False,
                        "error": "This number is already logged in elsewhere.",
                        "error_code": "ALREADY_AUTHORIZED"
                    }
            except:
                pass
            
            # Send verification code
            sent_code = await client.send_code(phone_number)
            
            # Generate session key
            session_key = hashlib.sha256(
                f"{phone_number}_{timestamp}_{secrets.token_hex(8)}".encode()
            ).hexdigest()[:32]
            
            # Store session data with lock
            with self.sessions_lock:
                self.login_sessions[session_key] = {
                    "client": client,
                    "device": device,
                    "phone": phone_number,
                    "timestamp": timestamp,
                    "phone_code_hash": sent_code.phone_code_hash,
                    "expires_at": timestamp + 300  # 5 minutes expiration
                }
            
            logger.info(f"OTP sent to {phone_number} via device: {device['device_model']}")
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key,
                "device_info": device,
                "expires_in": 300
            }
            
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"Flood wait for {phone_number}: {wait_time} seconds")
            
            if client:
                await self._safe_disconnect(client)
            
            return {
                "success": False,
                "error": f"Please wait {wait_time} seconds before trying again.",
                "error_code": "FLOOD_WAIT",
                "wait_time": wait_time
            }
            
        except PhoneNumberInvalid:
            logger.error(f"Invalid phone number: {phone_number}")
            self.stats["logins_failed"] += 1
            return {
                "success": False,
                "error": "Invalid phone number format.",
                "error_code": "INVALID_PHONE"
            }
            
        except Exception as e:
            logger.error(f"Send OTP error for {phone_number}: {e}", exc_info=True)
            self.stats["logins_failed"] += 1
            
            if client:
                await self._safe_disconnect(client)
            
            # Update error statistics
            error_name = type(e).__name__
            self.stats["errors"][error_name] = self.stats["errors"].get(error_name, 0) + 1
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "UNKNOWN_ERROR"
            }
    
    async def _verify_otp_async(self, session_key: str, otp_code: str, 
                                phone_number: str, phone_code_hash: str) -> Dict[str, Any]:
        """Async implementation of verify_otp"""
        try:
            # Check session exists with lock
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired or invalid. Please start again.",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                session_data = self.login_sessions[session_key]
                client = session_data["client"]
                device = session_data["device"]
            
            # Check expiration
            if time.time() > session_data["expires_at"]:
                await self._safe_disconnect(client)
                with self.sessions_lock:
                    del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": "Session expired. Please request new OTP.",
                    "error_code": "SESSION_EXPIRED"
                }
            
            try:
                # Attempt to sign in
                await client.sign_in(
                    phone_number=phone_number,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                has_2fa = False
                two_step_password = None
                
            except SessionPasswordNeeded:
                # 2FA required
                return {
                    "success": False,
                    "needs_2fa": True,
                    "session_key": session_key,
                    "device_info": device
                }
                
            except PhoneCodeInvalid:
                return {
                    "success": False,
                    "error": "Invalid verification code.",
                    "error_code": "INVALID_CODE"
                }
                
            except PhoneCodeExpired:
                return {
                    "success": False,
                    "error": "Verification code expired. Please request new one.",
                    "error_code": "CODE_EXPIRED"
                }
            
            # Success - get session string
            session_string = await client.export_session_string()
            
            # Get account info
            try:
                me = await client.get_me()
                account_info = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone_number": me.phone_number,
                    "is_bot": me.is_bot,
                    "is_premium": getattr(me, 'is_premium', False)
                }
            except:
                account_info = {}
            
            # Encrypt session string
            encrypted_session = self.encryption.encrypt(session_string)
            
            # Store active client with lock
            with self.sessions_lock:
                self.active_clients[phone_number] = client
                # Cleanup login session
                del self.login_sessions[session_key]
            
            # Update stats
            self.stats["logins_successful"] += 1
            
            logger.info(f"Login successful for {phone_number}, User ID: {account_info.get('user_id', 'N/A')}")
            
            return {
                "success": True,
                "session_string": encrypted_session,
                "raw_session": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password,
                "device_info": device,
                "account_info": account_info,
                "timestamp": int(time.time())
            }
            
        except Exception as e:
            logger.error(f"Verify OTP error: {e}", exc_info=True)
            
            # Cleanup on error with lock
            with self.sessions_lock:
                if session_key in self.login_sessions:
                    session_data = self.login_sessions[session_key]
                    asyncio.create_task(self._safe_disconnect(session_data["client"]))
                    del self.login_sessions[session_key]
            
            self.stats["logins_failed"] += 1
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "VERIFICATION_FAILED"
            }
    
    async def _verify_2fa_async(self, session_key: str, password: str) -> Dict[str, Any]:
        """Async implementation of verify_2fa with proper loop management"""
        try:
            # Check session exists with lock
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired. Please start again.",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                session_data = self.login_sessions[session_key]
                client = session_data["client"]
                device = session_data["device"]
            
            # Verify password using client's own event loop
            try:
                # Get client's current loop
                client_loop = asyncio.get_event_loop()
                
                # Run password check in client's loop context
                await client.check_password(password)
                
            except Exception as e:
                logger.error(f"2FA password check error: {e}")
                return {
                    "success": False,
                    "error": "Invalid 2FA password.",
                    "error_code": "INVALID_2FA"
                }
            
            # Get session string
            session_string = await client.export_session_string()
            
            # Get account info
            try:
                me = await client.get_me()
                account_info = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone_number": me.phone_number
                }
            except:
                account_info = {}
            
            # Encrypt session string
            encrypted_session = self.encryption.encrypt(session_string)
            
            # Cleanup with lock
            with self.sessions_lock:
                # Store active client
                if session_data.get("phone"):
                    self.active_clients[session_data["phone"]] = client
                
                # Remove from login sessions
                del self.login_sessions[session_key]
            
            # Update stats
            self.stats["logins_successful"] += 1
            
            logger.info(f"2FA login successful for {account_info.get('phone_number', 'unknown')}")
            
            return {
                "success": True,
                "session_string": encrypted_session,
                "raw_session": session_string,
                "has_2fa": True,
                "two_step_password": password,
                "device_info": device,
                "account_info": account_info,
                "timestamp": int(time.time())
            }
            
        except Exception as e:
            logger.error(f"2FA verification error: {e}", exc_info=True)
            
            # Cleanup on error with lock
            with self.sessions_lock:
                if session_key in self.login_sessions:
                    session_data = self.login_sessions[session_key]
                    asyncio.create_task(self._safe_disconnect(session_data["client"]))
                    del self.login_sessions[session_key]
            
            return {
                "success": False,
                "error": "Invalid 2FA password or connection error.",
                "error_code": "INVALID_2FA"
            }
    
    async def _get_latest_otp_async(self, session_string: str, phone: str, 
                                    max_messages: int = 50) -> Optional[str]:
        """Fetch latest OTP from messages"""
        client = None
        
        try:
            # Decrypt if encrypted
            try:
                decrypted_session = self.encryption.decrypt(session_string)
            except:
                decrypted_session = session_string
            
            # Get random device
            device = self.device_manager.get_random_device()
            
            # Create client with its own isolated context
            client = Client(
                name=f"otp_fetch_{int(time.time())}_{phone[-4:]}",
                session_string=decrypted_session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                in_memory=True,
                no_updates=True
            )
            
            # Connect and fetch OTP
            await client.connect()
            
            latest_otp = None
            latest_time = None
            
            # Check Telegram account (777000) first
            try:
                async for message in client.get_chat_history(777000, limit=max_messages):
                    if message.text:
                        # Look for OTP patterns
                        otp_matches = re.findall(r'\b\d{5}\b', message.text)
                        if otp_matches:
                            message_time = message.date.timestamp() if message.date else 0
                            if latest_time is None or message_time > latest_time:
                                latest_time = message_time
                                latest_otp = otp_matches[0]
                                
                        # Also check for 6-digit codes
                        if not latest_otp:
                            otp_matches = re.findall(r'\b\d{6}\b', message.text)
                            if otp_matches:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp_matches[0]
            except Exception as e:
                logger.warning(f"Error checking 777000: {e}")
            
            # Check "Telegram" chat if not found
            if not latest_otp:
                try:
                    async for message in client.get_chat_history("Telegram", limit=max_messages):
                        if message.text and ("code" in message.text.lower() or "verify" in message.text.lower()):
                            otp_matches = re.findall(r'\b\d{5,6}\b', message.text)
                            if otp_matches:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp_matches[0]
                except Exception as e:
                    logger.warning(f"Error checking Telegram chat: {e}")
            
            await self._safe_disconnect(client)
            
            if latest_otp:
                logger.info(f"OTP found for {phone}: {latest_otp}")
            
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP error for {phone}: {e}", exc_info=True)
            if client:
                await self._safe_disconnect(client)
            return None
    
    async def _validate_session_async(self, session_string: str, phone: str = None) -> Dict[str, Any]:
        """Validate if session is still active"""
        client = None
        
        try:
            # Decrypt if encrypted
            try:
                decrypted_session = self.encryption.decrypt(session_string)
            except:
                decrypted_session = session_string
            
            # Validate format
            if not self.validator.is_valid_session_format(decrypted_session):
                return {
                    "valid": False,
                    "error": "Invalid session format",
                    "error_code": "INVALID_FORMAT"
                }
            
            # Test session
            device = self.device_manager.get_random_device()
            
            client = Client(
                name=f"validate_{int(time.time())}",
                session_string=decrypted_session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            try:
                me = await client.get_me()
                
                # Check if user is banned/deleted
                if not me:
                    return {
                        "valid": False,
                        "error": "Account not found",
                        "error_code": "ACCOUNT_NOT_FOUND"
                    }
                
                account_info = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone_number": me.phone_number,
                    "is_bot": me.is_bot,
                    "is_premium": getattr(me, 'is_premium', False),
                    "is_deleted": me.is_deleted if hasattr(me, 'is_deleted') else False,
                    "is_restricted": me.is_restricted if hasattr(me, 'is_restricted') else False,
                    "is_scam": me.is_scam if hasattr(me, 'is_scam') else False,
                    "is_fake": me.is_fake if hasattr(me, 'is_fake') else False
                }
                
                # Test sending a request (get dialogs count)
                try:
                    dialogs = await client.get_dialogs(limit=1)
                    can_fetch = True
                except:
                    can_fetch = False
                
                await self._safe_disconnect(client)
                
                return {
                    "valid": True,
                    "account_info": account_info,
                    "can_fetch_messages": can_fetch,
                    "device_used": device,
                    "validated_at": int(time.time())
                }
                
            except AuthKeyUnregistered:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": "Session revoked or expired",
                    "error_code": "SESSION_REVOKED"
                }
            except UserDeactivatedBan:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": "Account banned",
                    "error_code": "ACCOUNT_BANNED"
                }
            except UserDeactivated:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": "Account deactivated",
                    "error_code": "ACCOUNT_DEACTIVATED"
                }
            except Exception as e:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": str(e),
                    "error_code": "VALIDATION_FAILED"
                }
            
        except Exception as e:
            logger.error(f"Session validation error: {e}")
            if client:
                await self._safe_disconnect(client)
            return {
                "valid": False,
                "error": str(e),
                "error_code": "CONNECTION_FAILED"
            }
    
    async def _get_account_info_async(self, session_string: str) -> Dict[str, Any]:
        """Get detailed account information"""
        validation = await self._validate_session_async(session_string)
        
        if not validation.get("valid"):
            return validation
        
        # Add additional info if valid
        client = None
        try:
            decrypted_session = self.encryption.decrypt(session_string)
            
            client = Client(
                name=f"info_{int(time.time())}",
                session_string=decrypted_session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            # Get more detailed info
            me = await client.get_me()
            
            # Get some stats
            try:
                dialogs_count = len(await client.get_dialogs(limit=100))
            except:
                dialogs_count = 0
            
            await self._safe_disconnect(client)
            
            result = validation.copy()
            result["additional_info"] = {
                "dialogs_count": dialogs_count,
                "has_username": bool(me.username),
                "has_profile_photo": bool(me.photo) if hasattr(me, 'photo') else False,
                "language": getattr(me, 'lang_code', 'en')
            }
            
            return result
            
        except Exception as e:
            if client:
                await self._safe_disconnect(client)
            
            result = validation.copy()
            result["additional_info_error"] = str(e)
            return result
    
    # ========================
    # UTILITY METHODS
    # ========================
    
    async def _safe_disconnect(self, client: Client):
        """Safely disconnect client"""
        try:
            if client and hasattr(client, 'is_connected') and client.is_connected:
                await client.disconnect()
                await asyncio.sleep(0.1)  # Small delay
        except Exception as e:
            logger.debug(f"Safe disconnect error: {e}")
    
    def _auto_cleanup(self):
        """Auto cleanup old sessions in background"""
        while True:
            try:
                time.sleep(60)  # Check every minute
                
                current_time = time.time()
                expired_keys = []
                
                # Get expired sessions with lock
                with self.sessions_lock:
                    for session_key, session_data in list(self.login_sessions.items()):
                        if current_time > session_data["expires_at"]:
                            expired_keys.append((session_key, session_data))
                
                # Cleanup expired sessions
                for session_key, session_data in expired_keys:
                    try:
                        if session_data.get("client"):
                            # Run disconnect in async context
                            asyncio.run(self._safe_disconnect(session_data["client"]))
                    except:
                        pass
                    
                    # Remove from sessions
                    with self.sessions_lock:
                        self.login_sessions.pop(session_key, None)
                
                if expired_keys:
                    logger.debug(f"Cleaned up {len(expired_keys)} expired sessions")
                    
            except Exception as e:
                logger.error(f"Auto cleanup error: {e}")
                time.sleep(10)
    
    def cleanup_sessions(self):
        """Manual cleanup of old sessions"""
        self.async_manager.run_async(self._cleanup_sessions_async())
    
    async def _cleanup_sessions_async(self):
        """Async cleanup implementation"""
        current_time = time.time()
        
        with self.sessions_lock:
            for session_key, session_data in list(self.login_sessions.items()):
                if current_time - session_data["timestamp"] > 1800:  # 30 minutes
                    client = session_data.get("client")
                    if client:
                        await self._safe_disconnect(client)
                    del self.login_sessions[session_key]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get usage statistics"""
        stats = self.stats.copy()
        with self.sessions_lock:
            stats.update({
                "active_sessions": len(self.login_sessions),
                "active_clients": len(self.active_clients),
                "uptime": int(time.time() - self.stats.get("start_time", time.time())),
                "encryption_enabled": hasattr(self.encryption, 'cipher')
            })
        return stats
    
    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "logins_attempted": 0,
            "logins_successful": 0,
            "logins_failed": 0,
            "otps_fetched": 0,
            "errors": {},
            "start_time": time.time()
        }
    
    def get_encryption_key(self) -> str:
        """Get current encryption key"""
        return self.encryption.get_key()
    
    def set_encryption_key(self, key: str):
        """Update encryption key"""
        self.encryption = EncryptionManager(key)
        logger.info("Encryption key updated")
    
    def disconnect_all(self):
        """Disconnect all active clients"""
        with self.sessions_lock:
            # Disconnect active clients
            for phone, client in list(self.active_clients.items()):
                try:
                    asyncio.run(self._safe_disconnect(client))
                except:
                    pass
            self.active_clients.clear()
            
            # Disconnect login sessions
            for session_key, session_data in list(self.login_sessions.items()):
                try:
                    asyncio.run(self._safe_disconnect(session_data["client"]))
                except:
                    pass
            self.login_sessions.clear()
    
    def __del__(self):
        """Cleanup on destruction"""
        self.disconnect_all()


# ========================
# QUICK START FUNCTION
# ========================
def create_account_manager(api_id: int, api_hash: str, 
                          encryption_key: Optional[str] = None) -> ProfessionalAccountManager:
    """
    Factory function to create Account Manager instance
    
    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        encryption_key: Optional encryption key (if not provided, will be generated)
    
    Returns:
        ProfessionalAccountManager instance
    """
    return ProfessionalAccountManager(api_id, api_hash, encryption_key)


# ========================
# TEST FUNCTION
# ========================
async def test_account_manager():
    """Test the account manager functionality"""
    print("Testing Account Manager...")
    
    # Replace with your API credentials
    API_ID = 123456  # Your API ID
    API_HASH = "your_api_hash_here"
    
    manager = ProfessionalAccountManager(API_ID, API_HASH)
    
    # Test stats
    print(f"Initial stats: {manager.get_stats()}")
    
    # Test session validation
    test_session = "test_session_string_here"
    if len(test_session) > 50:
        result = await manager._validate_session_async(test_session)
        print(f"Session validation: {result}")
    
    print("Test completed!")


if __name__ == "__main__":
    # Run test if executed directly
    asyncio.run(test_account_manager())
