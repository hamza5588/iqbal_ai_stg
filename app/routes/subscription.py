from flask import Blueprint, request, session, redirect, url_for, render_template, jsonify
from app.utils.auth import login_required
from app.utils.db import get_db
from app.models.database_models import User as DBUser, Coupon, CouponRedemption
from app.config import Config
from datetime import datetime
import stripe
import logging
import os

logger = logging.getLogger(__name__)
bp = Blueprint('subscription', __name__)

# Initialize Stripe
stripe.api_key = Config.STRIPE_SECRET_KEY

@bp.route('/settings', methods=['GET'])
@login_required
def settings():
    """Render the settings/subscription page"""
    try:
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not user:
            return redirect(url_for('auth.login'))
        
        # Get subscription details
        subscription_info = {
            'tier': user.subscription_tier or 'free',
            'status': user.subscription_status,
            'has_stripe_customer': bool(user.stripe_customer_id)
        }
        
        return render_template('settings.html', 
                             subscription=subscription_info,
                             stripe_publishable_key=Config.STRIPE_PUBLISHABLE_KEY)
    except Exception as e:
        logger.error(f"Error in settings route: {str(e)}")
        return redirect(url_for('chat.index'))

@bp.route('/api/subscription/plans', methods=['GET'])
@login_required
def get_plans():
    """Get available subscription plans"""
    try:
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        current_tier = user.subscription_tier if user else 'free'
        
        plans = [
            {
                'id': 'free',
                'name': 'Free',
                'price': 0,
                'features': [
                    'Basic AI chat functionality',
                    'Limited messages per day',
                    'Standard response speed',
                    'Community support'
                ],
                'current': current_tier == 'free'
            },
            {
                'id': 'pro',
                'name': 'Pro',
                'price': 19.99,
                'interval': 'month',
                'features': [
                    'Unlimited messages',
                    'Priority response speed',
                    'Advanced AI models',
                    'Email support',
                    'Export conversations'
                ],
                'current': current_tier == 'pro',
                'stripe_product_id': Config.STRIPE_PRO_PRODUCT_ID
            },
            {
                'id': 'pro_plus',
                'name': 'Pro Plus',
                'price': 49.99,
                'interval': 'month',
                'features': [
                    'Everything in Pro',
                    'API access',
                    'Custom integrations',
                    'Priority support',
                    'Advanced analytics'
                ],
                'current': current_tier == 'pro_plus',
                'coming_soon': True,
                'stripe_product_id': Config.STRIPE_PRO_PLUS_PRODUCT_ID
            }
        ]
        
        return jsonify({'plans': plans, 'current_tier': current_tier})
    except Exception as e:
        logger.error(f"Error getting plans: {str(e)}")
        return jsonify({'error': 'Failed to load plans'}), 500

@bp.route('/api/subscription/create-checkout', methods=['POST'])
@login_required
def create_checkout_session():
    """Create Stripe checkout session"""
    try:
        data = request.get_json()
        plan_id = data.get('plan_id')
        
        if not plan_id or plan_id == 'free' or plan_id == 'pro_plus':
            return jsonify({'error': 'Invalid plan'}), 400
        
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get or create Stripe customer
        customer_id = user.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=user.useremail,
                metadata={'user_id': user.id}
            )
            customer_id = customer.id
            user.stripe_customer_id = customer_id
            db.commit()
        
        # Get product ID and fetch the default price
        if plan_id == 'pro':
            product_id = Config.STRIPE_PRO_PRODUCT_ID
        elif plan_id == 'pro_plus':
            product_id = Config.STRIPE_PRO_PLUS_PRODUCT_ID
        else:
            return jsonify({'error': 'Invalid plan'}), 400
        
        if not product_id:
            return jsonify({'error': 'Product not configured'}), 500
        
        # Fetch the product to get its default price
        try:
            product = stripe.Product.retrieve(product_id)
            # Get the default price from the product
            prices = stripe.Price.list(product=product_id, active=True, limit=1)
            if not prices.data:
                return jsonify({'error': 'No active price found for product'}), 500
            price_id = prices.data[0].id
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error fetching product/price: {str(e)}")
            return jsonify({'error': 'Failed to fetch product details'}), 500
        
        # Create checkout session
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('subscription.checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('subscription.settings', _external=True),
            metadata={
                'user_id': user.id,
                'plan_id': plan_id
            }
        )
        
        return jsonify({'checkout_url': checkout_session.url})
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/checkout-success', methods=['GET'])
@login_required
def checkout_success():
    """Handle successful checkout"""
    try:
        session_id = request.args.get('session_id')
        if not session_id:
            return redirect(url_for('subscription.settings'))
        
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if user:
            user.stripe_subscription_id = checkout_session.subscription
            user.subscription_tier = checkout_session.metadata.get('plan_id', 'pro')
            user.subscription_status = 'active'
            db.commit()
        
        return redirect(url_for('subscription.settings') + '?success=true')
    except Exception as e:
        logger.error(f"Error processing checkout success: {str(e)}")
        return redirect(url_for('subscription.settings'))

@bp.route('/api/subscription/cancel', methods=['POST'])
@login_required
def cancel_subscription():
    """Cancel user subscription"""
    try:
        data = request.get_json() or {}
        immediate = data.get('immediate', False)  # Check if immediate downgrade is requested
        
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not user or not user.stripe_subscription_id:
            # If no subscription, just set to free
            user.subscription_tier = 'free'
            user.subscription_status = None
            db.commit()
            return jsonify({'success': True, 'message': 'Plan changed to Free'})
        
        if immediate:
            # Immediate downgrade to free - cancel subscription immediately
            try:
                stripe.Subscription.delete(user.stripe_subscription_id)
            except stripe.error.StripeError as e:
                logger.error(f"Error deleting Stripe subscription: {str(e)}")
                # Try to cancel at period end if immediate deletion fails
                stripe.Subscription.modify(
                    user.stripe_subscription_id,
                    cancel_at_period_end=True
                )
            
            # Immediately set user to free tier
            user.subscription_tier = 'free'
            user.subscription_status = None
            user.stripe_subscription_id = None
            db.commit()
            
            return jsonify({'success': True, 'message': 'Plan changed to Free'})
        else:
            # Cancel at period end (original behavior)
            stripe.Subscription.modify(
                user.stripe_subscription_id,
                cancel_at_period_end=True
            )
            
            user.subscription_status = 'canceled'
            db.commit()
            
            return jsonify({'success': True, 'message': 'Subscription will be canceled at the end of the billing period'})
    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = Config.STRIPE_WEBHOOK_SECRET
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        logger.error("Invalid payload")
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        logger.error("Invalid signature")
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        handle_checkout_completed(session_obj)
    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        handle_subscription_updated(subscription)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_deleted(subscription)
    
    return jsonify({'received': True})

def handle_checkout_completed(session_obj):
    """Handle completed checkout"""
    try:
        db = get_db()
        user_id = session_obj['metadata'].get('user_id')
        plan_id = session_obj['metadata'].get('plan_id')
        
        if user_id:
            user = db.query(DBUser).filter(DBUser.id == int(user_id)).first()
            if user:
                user.subscription_tier = plan_id
                user.subscription_status = 'active'
                user.stripe_subscription_id = session_obj.get('subscription')
                db.commit()
    except Exception as e:
        logger.error(f"Error handling checkout completed: {str(e)}")

def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    try:
        db = get_db()
        customer_id = subscription['customer']
        user = db.query(DBUser).filter(DBUser.stripe_customer_id == customer_id).first()
        
        if user:
            status = subscription['status']
            user.subscription_status = status
            
            if status in ['canceled', 'unpaid', 'past_due']:
                user.subscription_tier = 'free'
            
            db.commit()
    except Exception as e:
        logger.error(f"Error handling subscription updated: {str(e)}")

def handle_subscription_deleted(subscription):
    """Handle subscription deletion"""
    try:
        db = get_db()
        customer_id = subscription['customer']
        user = db.query(DBUser).filter(DBUser.stripe_customer_id == customer_id).first()
        
        if user:
            user.subscription_tier = 'free'
            user.subscription_status = None
            user.stripe_subscription_id = None
            db.commit()
    except Exception as e:
        logger.error(f"Error handling subscription deleted: {str(e)}")


@bp.route('/api/subscription/redeem-coupon', methods=['POST'])
@login_required
def redeem_coupon():
    """Redeem a coupon code for free subscription"""
    try:
        data = request.get_json()
        coupon_code = data.get('code', '').strip().upper()
        
        if not coupon_code:
            return jsonify({'error': 'Coupon code is required'}), 400
        
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Find the coupon
        coupon = db.query(Coupon).filter(Coupon.code == coupon_code).first()
        
        if not coupon:
            return jsonify({'error': 'Invalid coupon code'}), 400
        
        if not coupon.is_active:
            return jsonify({'error': 'This coupon is no longer active'}), 400
        
        # Check if coupon has expired
        if coupon.expires_at and coupon.expires_at < datetime.utcnow():
            return jsonify({'error': 'This coupon has expired'}), 400
        
        # Check if coupon has reached max uses
        if coupon.max_uses and coupon.used_count >= coupon.max_uses:
            return jsonify({'error': 'This coupon has reached its maximum usage limit'}), 400
        
        # Check if user has already redeemed this coupon
        existing_redemption = db.query(CouponRedemption).filter(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.user_id == user.id
        ).first()
        
        if existing_redemption:
            return jsonify({'error': 'You have already redeemed this coupon'}), 400
        
        # Cancel any existing Stripe subscription if present
        if user.stripe_subscription_id:
            try:
                stripe.Subscription.delete(user.stripe_subscription_id)
            except stripe.error.StripeError as e:
                logger.warning(f"Error deleting Stripe subscription: {str(e)}")
        
        # Apply the subscription tier from coupon
        user.subscription_tier = coupon.subscription_tier
        user.subscription_status = 'active'
        user.stripe_subscription_id = None  # No Stripe subscription for coupon-based subscriptions
        
        # Create redemption record
        redemption = CouponRedemption(
            coupon_id=coupon.id,
            user_id=user.id
        )
        db.add(redemption)
        
        # Update coupon usage count
        coupon.used_count += 1
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'Coupon redeemed successfully! You now have {coupon.subscription_tier.upper()} subscription.',
            'tier': coupon.subscription_tier
        })
        
    except Exception as e:
        logger.error(f"Error redeeming coupon: {str(e)}")
        db.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/subscription/coupons', methods=['GET'])
@login_required
def list_coupons():
    """List all coupons (admin only)"""
    try:
        # Check if user is admin
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not user or user.role != 'admin':
            return jsonify({'error': 'Unauthorized'}), 403
        
        coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()
        
        coupon_list = []
        for coupon in coupons:
            coupon_list.append({
                'id': coupon.id,
                'code': coupon.code,
                'subscription_tier': coupon.subscription_tier,
                'description': coupon.description,
                'max_uses': coupon.max_uses,
                'used_count': coupon.used_count,
                'expires_at': coupon.expires_at.isoformat() if coupon.expires_at else None,
                'is_active': coupon.is_active,
                'created_at': coupon.created_at.isoformat() if coupon.created_at else None
            })
        
        return jsonify({'coupons': coupon_list})
        
    except Exception as e:
        logger.error(f"Error listing coupons: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/subscription/coupons', methods=['POST'])
@login_required
def create_coupon():
    """Create a new coupon (admin only)"""
    try:
        # Check if user is admin
        db = get_db()
        user = db.query(DBUser).filter(DBUser.id == session['user_id']).first()
        
        if not user or user.role != 'admin':
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        code = data.get('code', '').strip().upper()
        subscription_tier = data.get('subscription_tier', 'pro')
        description = data.get('description', '')
        max_uses = data.get('max_uses')
        expires_at_str = data.get('expires_at')
        
        if not code:
            return jsonify({'error': 'Coupon code is required'}), 400
        
        if subscription_tier not in ['pro', 'pro_plus']:
            return jsonify({'error': 'Invalid subscription tier'}), 400
        
        # Check if coupon code already exists
        existing = db.query(Coupon).filter(Coupon.code == code).first()
        if existing:
            return jsonify({'error': 'Coupon code already exists'}), 400
        
        # Parse expiration date if provided
        expires_at = None
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            except:
                return jsonify({'error': 'Invalid expiration date format'}), 400
        
        # Create coupon
        coupon = Coupon(
            code=code,
            subscription_tier=subscription_tier,
            description=description,
            max_uses=max_uses,
            expires_at=expires_at,
            is_active=True,
            created_by=user.id
        )
        
        db.add(coupon)
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Coupon created successfully',
            'coupon': {
                'id': coupon.id,
                'code': coupon.code,
                'subscription_tier': coupon.subscription_tier
            }
        })
        
    except Exception as e:
        logger.error(f"Error creating coupon: {str(e)}")
        db.rollback()
        return jsonify({'error': str(e)}), 500

