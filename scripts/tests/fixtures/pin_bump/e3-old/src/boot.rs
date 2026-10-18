//! A module's boot, cut down to the shapes the E3 boot audit reads.

pub fn env_required(key: &str) -> Result<String, String> {
    std::env::var(key).map_err(|_| format!("{key} is required"))
}

fn env_required_chart(env: &impl Fn(&str) -> Option<String>, key: &str, chart_key: &str) -> Result<String, String> {
    env(key).ok_or_else(|| format!("{key} ({chart_key}) is required"))
}

pub fn boot(env: &impl Fn(&str) -> Option<String>) -> Result<(), String> {
    let _listen = env_required("LISTEN")?;
    let _host = env_required_chart(env, "DB_HOST", "database.host")?;
    let _sni = std::env::var("NATS_TLS_DOMAIN").ok();
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_test_only_key_is_not_a_boot_key() {
        let _ = super::env_required("YADGAR_TEST_ONLY_KEY");
    }
}
