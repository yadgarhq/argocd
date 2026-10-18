//! Braces inside literals and comments, the shapes a naive brace counter
//! miscounts. The test module below opens a `{` inside a string, a char literal
//! and a comment; the production read after it must still be seen.

const AFTER_KEY: &str = "AFTER_CONST_KEY";

pub fn env_required(key: &str) -> Result<String, String> {
    std::env::var(key).map_err(|_| format!("{key} is required"))
}

#[cfg(test)]
mod tests {
    // an unbalanced brace in a comment: {
    /* and in a block comment: { { */
    #[test]
    fn braces_in_literals() {
        let open = "{";
        let raw = r#"{ "nested": "{" }"#;
        let ch = '{';
        let _ = (open, raw, ch, super::env_required("TEST_ONLY_KEY"));
    }
}

pub fn boot(lookup: impl Fn(&str) -> Option<String>) -> Result<(), String> {
    let _after = env_required("AFTER_TEST_MODULE")?;
    let _const = env_required(AFTER_KEY)?;
    let _optional = lookup("LOOKUP_KEY");
    let _not_env = "PROJECT_ARCHIVED";
    Ok(())
}
