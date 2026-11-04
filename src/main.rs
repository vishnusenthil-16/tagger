mod backend;
mod config;
mod error;
mod schema;
mod server;

use clap::Parser;
#[derive(Parser)]
#[command(version, about = "Run the Tagger categorization service")]
struct Args {
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 8080)]
    port: u16,
}
#[tokio::main]
async fn main() -> std::process::ExitCode {
    let args = Args::parse();
    let settings = match config::Settings::from_env() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("{e}");
            return std::process::ExitCode::from(2);
        }
    };
    if server::run(&args.host, args.port, settings).await.is_err() {
        eprintln!("Tagger could not start or accept connections.");
        return std::process::ExitCode::FAILURE;
    }
    std::process::ExitCode::SUCCESS
}
