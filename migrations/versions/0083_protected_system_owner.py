"""Protect the single system owner against revocation and account disablement."""
import sqlalchemy as sa
from alembic import op

revision = "0083_protected_system_owner"
down_revision = "0082_resilient_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_single_super_admin", "admin_grants", ["role"], unique=True,
                    postgresql_where=sa.text("role = 'SUPER_ADMIN'"))
    op.execute("""
        CREATE FUNCTION protect_system_owner_grant() RETURNS trigger AS $$
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.role = 'SUPER_ADMIN' THEN
                RAISE EXCEPTION 'The system owner grant is immutable';
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.role = 'SUPER_ADMIN' THEN
                IF NOT NEW.active OR NEW.revoked_at IS NOT NULL THEN
                    RAISE EXCEPTION 'The system owner must remain active';
                END IF;
                PERFORM id FROM users WHERE id = NEW.user_id AND status = 'ACTIVE' FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'The system owner account must be active';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER protect_system_owner_grant
        BEFORE INSERT OR UPDATE OR DELETE ON admin_grants
        FOR EACH ROW EXECUTE FUNCTION protect_system_owner_grant();

        CREATE FUNCTION protect_system_owner_account() RETURNS trigger AS $$
        BEGIN
            IF NEW.status <> 'ACTIVE' AND EXISTS (
                SELECT 1 FROM admin_grants WHERE user_id = OLD.id AND role = 'SUPER_ADMIN'
            ) THEN
                RAISE EXCEPTION 'The system owner cannot be disabled';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER protect_system_owner_account
        BEFORE UPDATE OF status ON users
        FOR EACH ROW EXECUTE FUNCTION protect_system_owner_account();

        CREATE FUNCTION protect_system_owner_truncate() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM admin_grants WHERE role = 'SUPER_ADMIN') THEN
                RAISE EXCEPTION 'Truncation would remove the system owner';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER protect_system_owner_truncate BEFORE TRUNCATE ON admin_grants
        FOR EACH STATEMENT EXECUTE FUNCTION protect_system_owner_truncate();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM admin_grants WHERE role = 'SUPER_ADMIN') THEN
                RAISE EXCEPTION 'Cannot remove protection while a system owner exists';
            END IF;
        END $$;
        DROP TRIGGER protect_system_owner_truncate ON admin_grants;
        DROP FUNCTION protect_system_owner_truncate();
        DROP TRIGGER protect_system_owner_account ON users;
        DROP FUNCTION protect_system_owner_account();
        DROP TRIGGER protect_system_owner_grant ON admin_grants;
        DROP FUNCTION protect_system_owner_grant();
    """)
    op.drop_index("uq_single_super_admin", table_name="admin_grants")
