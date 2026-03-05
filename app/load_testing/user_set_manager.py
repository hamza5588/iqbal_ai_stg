import logging
import uuid
import random
import string
from typing import List, Optional, Tuple
from sqlalchemy.exc import IntegrityError
from app.utils.db import get_db
from app.models.database_models import User
from app.load_testing.models import TestUserSet, TestUser

logger = logging.getLogger(__name__)

class UserSetManager:
    """Manages the lifecycle of test user sets"""
    
    @staticmethod
    def create_user_set(
        name: str, 
        role: str, 
        count: int, 
        password: str = "TestPass123!"
    ) -> Tuple[Optional[TestUserSet], List[str]]:
        """
        Create a new set of test users.
        Returns (TestUserSet object, list of error messages)
        """
        db = get_db()
        errors = []
        
        try:
            # Create the set record first
            user_set = TestUserSet(
                name=name,
                role=role,
                user_count=count
            )
            db.add(user_set)
            db.commit()
            
            logger.info(f"Created TestUserSet {user_set.id}: {name} ({count} {role}s)")
            
            # Generate users
            created_users = []
            
            for i in range(count):
                # Generate unique email
                # format: loadtest_{role}_{index}_{set_id}_{random_suffix}@iqbalai.com
                suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
                email = f"loadtest_{role}_{i+1}_{user_set.id}_{suffix}@test.iqbalai.com"
                username = f"LoadTest {role.capitalize()} {i+1} (Set {user_set.id})"
                
                try:
                    # 1. Create real user in main users table
                    # Note: We're not hashing passwords for load tests to keep it simple/fast 
                    # and because these are temporary test accounts. 
                    # If the app requires hashed passwords, we should use the auth util.
                    # Assuming plain text for now based on typical load test setups, 
                    # but if auth requires hashing, we'll need to import generate_password_hash
                    
                    real_user = User(
                        username=username,
                        useremail=email,
                        password=password, # Validating against auth.py which uses plain text comparison
                        role=role,
                        class_standard='10th' if role == 'student' else 'NA',
                        medium='English',
                        groq_api_key='test_key_placeholder', # Placeholder
                        subscription_tier='free'
                    )
                    db.add(real_user)
                    db.flush() # Get the ID
                    
                    # 2. Create test user record
                    test_user = TestUser(
                        user_set_id=user_set.id,
                        email=email,
                        password=password, # Store plain text for the runner to use
                        real_user_id=real_user.id,
                        is_active=True
                    )
                    db.add(test_user)
                    created_users.append(test_user)
                    
                except IntegrityError as e:
                    db.rollback()
                    msg = f"Failed to create user {email}: {str(e)}"
                    logger.error(msg)
                    errors.append(msg)
                    # Continue with other users
                    continue
                except Exception as e:
                    db.rollback()
                    msg = f"Error creating user {email}: {str(e)}"
                    logger.error(msg)
                    errors.append(msg)
                    continue

            db.commit()
            
            # Update actual count if some failed
            if len(created_users) != count:
                user_set.user_count = len(created_users)
                db.commit()
                
            return user_set, errors

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create user set: {str(e)}")
            errors.append(f"Fatal error: {str(e)}")
            return None, errors

    @staticmethod
    def delete_user_set(set_id: int) -> bool:
        """
        Delete a user set and all associated real users.
        """
        db = get_db()
        try:
            user_set = db.query(TestUserSet).get(set_id)
            if not user_set:
                return False
            
            # Get all test users to find their real_user_ids
            test_users = db.query(TestUser).filter_by(user_set_id=set_id).all()
            real_user_ids = [tu.real_user_id for tu in test_users if tu.real_user_id]
            
            # Delete real users from main table
            if real_user_ids:
                # This might cascade delete other things depending on models
                # But explicit delete is safer
                db.query(User).filter(User.id.in_(real_user_ids)).delete(synchronize_session=False)
            
            # Delete the test set (cascades to test_users)
            db.delete(user_set)
            
            db.commit()
            logger.info(f"Deleted user set {set_id} and {len(real_user_ids)} real users")
            return True
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting user set {set_id}: {str(e)}")
            return False

    @staticmethod
    def get_all_sets() -> List[TestUserSet]:
        """Get all user sets"""
        db = get_db()
        return db.query(TestUserSet).order_by(TestUserSet.created_at.desc()).all()

    @staticmethod
    def get_set(set_id: int) -> Optional[TestUserSet]:
        """Get a specific user set"""
        db = get_db()
        return db.query(TestUserSet).get(set_id)
