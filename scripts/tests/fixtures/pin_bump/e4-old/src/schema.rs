//! A -db module's migrations, split across `schema.rs` and a child module as
//! iam-db's are (`src/schema/policy.rs`).

mod policy;

pub fn migrations() -> Vec<Migration> {
    vec![user(), policy::setting()]
}

fn user() -> Migration {
    Migration {
        version: 1,
        name: "create_user".into(),
        sql: "CREATE TABLE user (id BIGINT PRIMARY KEY)".into(),
    }
}

#[cfg(test)]
mod tests {
    fn a_test_migration() -> super::Migration {
        super::Migration {
            version: 99,
            name: "test_only".into(),
            sql: "SELECT 1".into(),
        }
    }
}
