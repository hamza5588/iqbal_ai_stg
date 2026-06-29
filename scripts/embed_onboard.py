#!/usr/bin/env python3
"""Onboard embed client after document upload."""
import argparse, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import create_app
from app.services.embed_service import create_embed_client, get_client_by_slug, update_embed_client

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--slug', required=True)
    p.add_argument('--owner-email', required=True)
    p.add_argument('--rag-thread-id')
    p.add_argument('--service-user-id', type=int)
    p.add_argument('--origins', default='')
    args = p.parse_args()
    origins = [o.strip() for o in args.origins.split(',') if o.strip()]
    app = create_app()
    with app.app_context():
        ex = get_client_by_slug(args.slug)
        if ex:
            update_embed_client(ex.id, owner_email=args.owner_email,
                rag_thread_id=args.rag_thread_id, service_user_id=args.service_user_id,
                allowed_origins=origins or None)
            print(f'Updated client {args.slug}')
            return
        client, secret = create_embed_client(
            client_slug=args.slug, owner_email=args.owner_email,
            rag_thread_id=args.rag_thread_id, service_user_id=args.service_user_id,
            allowed_origins=origins)
        print(f'Created {client.client_slug} — CLIENT_KEY: {secret}')

if __name__ == '__main__':
    main()
