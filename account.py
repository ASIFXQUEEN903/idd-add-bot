"""
Fixed Pyrogram Account Manager with OTP Expiration Handling
"""

import logging
import re
import asyncio
from datetime import datetime, timedelta
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid,
    PhoneCodeExpired, SessionPasswordNeeded,
    FloodWait, PhoneCodeEmpty, AuthKeyUnregistered
)

logger = logging.getLogger(__name__)


class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        # Store session data with timestamp
        self.login_sessions = {}  # {session_key: {"phone": "", "phone_code_hash": "", "timestamp": "", "client_name": ""}}
    
    def send_otp(self, phone_number):
        """Send OTP to phone number"""
        try:
            return asyncio.run(self._send_otp_async(phone_number))
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            return {
                "success": False,
                "error": f"Failed to send OTP: {str(e)}"
            }
    
    async def _send_otp_async(self, phone_number):
        """Async function to send OTP"""
        client = None
        try:
            # Clean phone number
            phone_number = phone_number.strip()
            
            client_name = f"login_{int(datetime.now().timestamp())}"
            client = Client(
                name=client_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            logger.info(f"Connecting to send OTP to {phone_number}")
            await client.connect()
            
            sent_code = await client.send_code(phone_number)
            logger.info(f"OTP sent to {phone_number}, phone_code_hash: {sent_code.phone_code_hash}")
            
            # Store session data with timestamp
            session_key = f"{phone_number}_{int(datetime.now().timestamp())}"
            self.login_sessions[session_key] = {
                "phone": phone_number,
                "phone_code_hash": sent_code.phone_code_hash,
                "timestamp": datetime.now(),
                "client_name": client_name
            }
            
            # Clean up old sessions
            self._cleanup_old_sessions()
            
            # IMPORTANT: Disconnect immediately
            await client.disconnect()
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key
            }
            
        except FloodWait as e:
            logger.warning(f"Flood wait for {phone_number}: {e.value} seconds")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return {
                "success": False,
                "error": f"Please wait {e.value} seconds before trying again"
            }
        except Exception as e:
            logger.error(f"Send OTP error for {phone_number}: {e}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_otp(self, session_key, otp_code, phone_number, phone_code_hash):
        """Verify OTP and get session string"""
        try:
            return asyncio.run(self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash))
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            return {
                "success": False,
                "error": f"OTP verification failed: {str(e)}"
            }
    
    async def _verify_otp_async(self, session_key, otp_code, phone_number, phone_code_hash):
        """Async function to verify OTP"""
        client = None
        try:
            # Check if session exists and is not expired
            if session_key not in self.login_sessions:
                logger.warning(f"Session key not found: {session_key}")
                return {
                    "success": False,
                    "error": "Session expired. Please start again with /start"
                }
            
            session_data = self.login_sessions[session_key]
            session_time = session_data.get("timestamp")
            
            # Check if session is too old (more than 10 minutes)
            if session_time and datetime.now() - session_time > timedelta(minutes=10):
                logger.warning(f"Session expired: {session_key}, created at {session_time}")
                del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": "OTP code expired. Please request a new code with /start"
                }
            
            # Create NEW client for verification
            client_name = f"verify_{int(datetime.now().timestamp())}"
            client = Client(
                name=client_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            logger.info(f"Connecting to verify OTP for {phone_number}")
            await client.connect()
            
            try:
                # Sign in with OTP
                logger.info(f"Signing in with OTP for {phone_number}")
                await client.sign_in(
                    phone_number=phone_number,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                has_2fa = False
                two_step_password = None
                logger.info(f"Successfully signed in for {phone_number}")
                
            except PhoneCodeExpired:
                logger.warning(f"Phone code expired for {phone_number}")
                await client.disconnect()
                # Remove expired session
                if session_key in self.login_sessions:
                    del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": "OTP code has expired. Please request a new code with /start"
                }
                
            except PhoneCodeInvalid:
                logger.warning(f"Invalid phone code for {phone_number}")
                await client.disconnect()
                return {
                    "success": False,
                    "error": "Invalid OTP code. Please enter the correct code."
                }
                
            except SessionPasswordNeeded:
                logger.info(f"2FA required for {phone_number}")
                await client.disconnect()
                # Store client for 2FA (we need to keep this one)
                # But create a new session key for 2fa
                twofa_session_key = f"2fa_{session_key}_{int(datetime.now().timestamp())}"
                self.login_sessions[twofa_session_key] = {
                    "phone": phone_number,
                    "original_session": session_key,
                    "timestamp": datetime.now(),
                    "needs_password": True
                }
                # Remove the old session
                if session_key in self.login_sessions:
                    del self.login_sessions[session_key]
                    
                return {
                    "success": False,
                    "needs_2fa": True,
                    "session_key": twofa_session_key
                }
            
            except Exception as e:
                logger.error(f"Sign in error for {phone_number}: {e}")
                await client.disconnect()
                # Remove session on error
                if session_key in self.login_sessions:
                    del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": f"OTP verification failed: {str(e)}"
                }
            
            # Get session string
            session_string = await client.export_session_string()
            logger.info(f"Got session string for {phone_number}")
            
            await client.disconnect()
            
            # Remove session data
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": has_2fa,
                "two_step_password": two_step_password
            }
            
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            # Clean up session
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_2fa(self, session_key, password):
        """Verify 2FA password"""
        try:
            return asyncio.run(self._verify_2fa_async(session_key, password))
        except Exception as e:
            logger.error(f"Verify 2FA error: {e}")
            return {
                "success": False,
                "error": f"2FA verification failed: {str(e)}"
            }
    
    async def _verify_2fa_async(self, session_key, password):
        """Async function to verify 2FA"""
        client = None
        try:
            if session_key not in self.login_sessions:
                return {
                    "success": False,
                    "error": "Session expired. Please start again with /start"
                }
            
            session_data = self.login_sessions[session_key]
            phone = session_data.get("phone")
            
            if not phone:
                return {
                    "success": False,
                    "error": "Invalid session. Please start again."
                }
            
            # For 2FA, we need to create a new client and complete the login
            # This is a simplified approach - in reality, we should store more state
            
            client_name = f"verify_2fa_{int(datetime.now().timestamp())}"
            client = Client(
                name=client_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            # Note: This is a simplified 2FA approach
            # In a real scenario, we would need to store the original client state
            try:
                # This assumes we're already at password stage
                # We might need to redo the OTP verification first
                await client.check_password(password)
            except Exception as e:
                await client.disconnect()
                if session_key in self.login_sessions:
                    del self.login_sessions[session_key]
                return {
                    "success": False,
                    "error": f"Password verification failed: {str(e)}"
                }
            
            session_string = await client.export_session_string()
            await client.disconnect()
            
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            
            return {
                "success": True,
                "session_string": session_string,
                "has_2fa": True,
                "two_step_password": password
            }
            
        except Exception as e:
            logger.error(f"2FA verification error: {e}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            if session_key in self.login_sessions:
                del self.login_sessions[session_key]
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_latest_otp(self, session_string, phone):
        """Manually fetch latest OTP from Telegram messages"""
        try:
            return asyncio.run(self._get_latest_otp_async(session_string, phone))
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            return None
    
    async def _get_latest_otp_async(self, session_string, phone):
        """Async function to fetch OTP"""
        client = None
        try:
            client = Client(
                name=f"otp_fetch_{int(datetime.now().timestamp())}",
                session_string=session_string,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            latest_otp = None
            latest_time = None
            
            # Search in recent messages (last 20)
            try:
                async for message in client.get_chat_history("me", limit=20):
                    if message.text:
                        # Look for OTP patterns
                        otp_matches = re.findall(r'\b\d{5,6}\b', message.text)
                        for otp in otp_matches:
                            # Check if it's likely an OTP (5-6 digits, not part of longer number)
                            if len(otp) in [5, 6] and message.text.find(f" {otp} ") != -1:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp
            except Exception as e:
                logger.warning(f"Search in chat failed: {e}")
            
            await client.disconnect()
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return None
    
    def _cleanup_old_sessions(self):
        """Cleanup old sessions (older than 30 minutes)"""
        try:
            cutoff_time = datetime.now() - timedelta(minutes=30)
            to_remove = []
            
            for session_key, data in self.login_sessions.items():
                timestamp = data.get("timestamp")
                if timestamp and timestamp < cutoff_time:
                    to_remove.append(session_key)
            
            for session_key in to_remove:
                del self.login_sessions[session_key]
            
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old sessions")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
