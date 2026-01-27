"""
Admin initialization utility
Creates default admin account on server startup if it doesn't exist
"""
import logging
from app.models import UserModel
from app.utils.db import get_db
from app.models.database_models import User as DBUser
from sqlalchemy import text

logger = logging.getLogger(__name__)


def create_default_admin():
    """
    Create default admin account if it doesn't exist.
    If admin username/email exists, creates with alternative name (admin2, admin3, etc.)
    Username: admin (or admin2, admin3, etc. if taken)
    Password: 123456
    Email: admin@iqbalai.com (or admin2@iqbalai.com, etc. if taken)
    """
    try:
        db = get_db()
        
        # First, ensure the database constraint allows 'admin' role
        try:
            # Try to update the constraint if it exists (PostgreSQL)
            db.execute(text("""
                ALTER TABLE users DROP CONSTRAINT IF EXISTS check_user_role;
                ALTER TABLE users ADD CONSTRAINT check_user_role 
                CHECK (role IN ('student', 'teacher', 'admin'));
            """))
            db.commit()
            logger.info("Updated check_user_role constraint to include 'admin'")
        except Exception as constraint_error:
            # Constraint update might fail if it doesn't exist or is different
            # This is okay, we'll try to create the user anyway
            db.rollback()
            logger.debug(f"Constraint update attempt: {str(constraint_error)}")
        
        base_username = 'admin'
        base_email = 'admin@iqbalai.com'
        password = '123456'
        
        # Find an available username and email
        username = base_username
        email = base_email
        counter = 1
        
        while True:
            # Check if username or email already exists
            existing_user = db.query(DBUser).filter(
                (DBUser.username == username) | (DBUser.useremail == email)
            ).first()
            
            if existing_user:
                # If existing user is already an admin, we're done
                if existing_user.role == 'admin':
                    logger.info(f"Admin account already exists: {username} ({email})")
                    logger.info(f"Admin can login with email: {existing_user.useremail}")
                    return existing_user.id
                
                # Try next available username/email
                counter += 1
                username = f'{base_username}{counter}'
                email = f'admin{counter}@iqbalai.com'
            else:
                # Username and email are available
                break
        
        # Create new admin account with available username/email
        # Use direct database insertion to bypass any constraint issues
        try:
            # Try using UserModel first
            admin_id = UserModel.create_user(
                username=username,
                useremail=email,
                password=password,
                class_standard='N/A',
                medium='N/A',
                groq_api_key='',
                role='admin'
            )
            
            logger.info(f"Default admin account created successfully (ID: {admin_id})")
            logger.info(f"Admin login credentials - Username: {username}, Email: {email}, Password: {password}")
            return admin_id
            
        except (ValueError, Exception) as e:
            # If UserModel.create_user fails, try direct database insertion
            logger.warning(f"UserModel.create_user failed: {str(e)}, trying direct database insertion...")
            
            try:
                # Check if user already exists first
                existing_check = db.execute(text("""
                    SELECT id, role FROM users WHERE username = :username OR useremail = :email
                """), {'username': username, 'email': email}).fetchone()
                
                if existing_check:
                    user_id, current_role = existing_check
                    if current_role != 'admin':
                        # Update existing user to admin
                        db.execute(text("""
                            UPDATE users SET role = 'admin' WHERE id = :id
                        """), {'id': user_id})
                        db.commit()
                        logger.info(f"Updated existing user to admin (ID: {user_id})")
                    else:
                        logger.info(f"User already exists as admin (ID: {user_id})")
                    logger.info(f"Admin login credentials - Username: {username}, Email: {email}, Password: {password}")
                    return user_id
                
                # Insert new admin user (constraint should be updated by now)
                result = db.execute(text("""
                    INSERT INTO users (username, useremail, password, role, class_standard, medium, groq_api_key, subscription_tier)
                    VALUES (:username, :email, :password, 'admin', 'N/A', 'N/A', '', 'free')
                    RETURNING id
                """), {
                    'username': username,
                    'email': email,
                    'password': password
                })
                
                row = result.fetchone()
                if row:
                    admin_id = row[0]
                    db.commit()
                    logger.info(f"Admin account created via direct SQL (ID: {admin_id})")
                    logger.info(f"Admin login credentials - Username: {username}, Email: {email}, Password: {password}")
                    return admin_id
                else:
                    raise Exception("Failed to create admin user - no ID returned")
                    
            except Exception as sql_error:
                logger.error(f"Direct SQL insertion also failed: {str(sql_error)}")
                db.rollback()
                
                # Last resort: try to find any existing admin
                existing_admin = db.query(DBUser).filter(DBUser.role == 'admin').first()
                if existing_admin:
                    logger.info(f"Found existing admin account: {existing_admin.username} ({existing_admin.useremail})")
                    return existing_admin.id
                raise
        
    except Exception as e:
        logger.error(f"Error creating default admin account: {str(e)}", exc_info=True)
        # Don't raise - allow server to start even if admin creation fails
        # Try to find any existing admin account
        try:
            db = get_db()
            existing_admin = db.query(DBUser).filter(DBUser.role == 'admin').first()
            if existing_admin:
                logger.info(f"Found existing admin account: {existing_admin.username} ({existing_admin.useremail})")
                return existing_admin.id
        except:
            pass
        return None

  