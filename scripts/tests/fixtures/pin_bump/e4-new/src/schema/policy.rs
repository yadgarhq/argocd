pub fn setting() -> super::Migration {
    super::Migration {
        version: 2,
        name: "create_setting".into(),
        sql: "CREATE TABLE setting (name VARCHAR(64) PRIMARY KEY)".into(),
    }
}
