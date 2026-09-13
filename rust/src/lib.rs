//! PACT v0.3 dual-profile wire codec — Rust reference implementation.
//! Mirrors `src/pact/__init__.py`. Same canonicalization => same sid.
//! Fail-closed: every violation returns Err(PactError).
//!
//! Two wire profiles share one schema/sid:
//!   * machine (codec->codec): leading `name[N]` count, enforced. `encode`.
//!   * model (LLM-emitted): `name[*]` + a trailing `#n=<count>` line after the
//!     last row. `encode_model`. decode enforces the trailing count when
//!     present; a `name[*]` with no trailer decodes with count_verified=false.
//!
//! v0.3 adds an OPTIONAL inline field echo on the body line —
//! `name[*]{f1,f2,...}` — a pure rendering of the negotiated schema. It does
//! not change canonicalization or the sid. decode verifies any echo against
//! the schema fail-closed; the echo is optional (both forms decode).

use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq)]
pub enum FType {
    Str,
    Int,
    Float,
    Bool,
    Enum(Vec<String>),
}

#[derive(Debug, Clone)]
pub struct Field {
    pub name: String,
    pub ftype: FType,
    pub lo: Option<f64>,
    pub hi: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct Schema {
    pub name: String,
    pub version: String,
    pub fields: Vec<Field>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
}

/// Decoded message. `count_verified` is false only for a model-profile
/// `name[*]` with no trailing `#n=` line (count derived, not verified).
#[derive(Debug, PartialEq)]
pub struct Decoded {
    pub records: Vec<Vec<Value>>,
    pub count_verified: bool,
}

#[derive(Debug)]
pub struct PactError(pub String);

impl std::fmt::Display for PactError {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "PactError: {}", self.0)
    }
}
impl std::error::Error for PactError {}

impl Schema {
    pub fn canonical(&self) -> String {
        let mut parts = vec![format!("{}@{}", self.name, self.version)];
        for f in &self.fields {
            let mut s = match &f.ftype {
                FType::Str => format!("{}:str", f.name),
                FType::Int => format!("{}:int", f.name),
                FType::Float => format!("{}:float", f.name),
                FType::Bool => format!("{}:bool", f.name),
                FType::Enum(vs) => format!("{}:enum{{{}}}", f.name, vs.join("|")),
            };
            if f.lo.is_some() || f.hi.is_some() {
                // Format bounds by FIELD TYPE, not value, for parity with
                // Python/TS: an int field prints "50" (not "50.0"); a float
                // field prints "0.0"/"1.0". (Python stores the literal type;
                // TS boundRepr keys off ftype — Rust must do the same.)
                let fmt_bound = |x: f64| -> String {
                    match f.ftype {
                        FType::Int => (x as i64).to_string(),
                        _ => fmt_num(x),
                    }
                };
                s += &format!(
                    "[{},{}]",
                    f.lo.map(fmt_bound).unwrap_or("None".into()),
                    f.hi.map(fmt_bound).unwrap_or("None".into())
                );
            }
            parts.push(s);
        }
        parts.join(";")
    }

    pub fn sid(&self) -> String {
        let h = Sha256::digest(self.canonical().as_bytes());
        hex::encode(&h)[..8].to_string()
    }
}

/// Match Python float repr for canonicalization parity (50 -> "50.0", 0.5 -> "0.5").
fn fmt_num(x: f64) -> String {
    if x.fract() == 0.0 {
        format!("{:.1}", x)
    } else {
        format!("{}", x)
    }
}

fn esc(v: &Value) -> String {
    match v {
        Value::Null => "~".into(),
        Value::Bool(true) => "T".into(),
        Value::Bool(false) => "F".into(),
        Value::Int(i) => i.to_string(),
        Value::Float(x) => x.to_string(),
        Value::Str(s) => s
            .replace('\\', "\\\\")
            .replace(',', "\\,")
            .replace('\n', "\\n"),
    }
}

fn unesc(s: &str) -> String {
    let mut out = String::new();
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '\\' {
            match it.next() {
                Some('n') => out.push('\n'),
                Some(x) => out.push(x),
                None => out.push('\\'),
            }
        } else {
            out.push(c);
        }
    }
    out
}

fn split_unescaped(line: &str) -> Vec<String> {
    let mut cells = vec![String::new()];
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\\' {
            if let Some(&n) = chars.peek() {
                cells.last_mut().unwrap().push('\\');
                cells.last_mut().unwrap().push(n);
                chars.next();
            }
        } else if c == ',' {
            cells.push(String::new());
        } else {
            cells.last_mut().unwrap().push(c);
        }
    }
    cells
}

pub fn encode(
    records: &[Vec<Value>],
    schema: &Schema,
    msg_type: &str,
    sender: &str,
    receiver: &str,
    corr: &str,
) -> String {
    let mut lines = vec![
        format!(
            ">{} s={} r={} c={} sid={}",
            msg_type, sender, receiver, corr, schema.sid()
        ),
        format!("{}[{}]", schema.name, records.len()),
    ];
    for rec in records {
        lines.push(rec.iter().map(esc).collect::<Vec<_>>().join(","));
    }
    lines.join("\n")
}

/// Model profile (v0.2): `name[*]` header + value rows + trailing `#n=<count>`.
/// The LLM-emitted profile; `encode` remains the machine profile.
pub fn encode_model(
    records: &[Vec<Value>],
    schema: &Schema,
    msg_type: &str,
    sender: &str,
    receiver: &str,
    corr: &str,
    field_echo: bool,
) -> String {
    let echo = if field_echo {
        format!(
            "{{{}}}",
            schema.fields.iter().map(|f| f.name.as_str()).collect::<Vec<_>>().join(",")
        )
    } else {
        String::new()
    };
    let mut lines = vec![
        format!(
            ">{} s={} r={} c={} sid={}",
            msg_type, sender, receiver, corr, schema.sid()
        ),
        format!("{}[*]{}", schema.name, echo),
    ];
    for rec in records {
        lines.push(rec.iter().map(esc).collect::<Vec<_>>().join(","));
    }
    lines.push(format!("#n={}", records.len()));
    lines.join("\n")
}

fn is_trailer(line: &str) -> Option<usize> {
    // Mirror Python/TS anchored /^#n=(\d+)$/: whole line is #n=<digits>.
    line.strip_prefix("#n=")
        .filter(|r| !r.is_empty() && r.bytes().all(|b| b.is_ascii_digit()))
        .map(|r| r.parse().unwrap())
}

pub fn decode(wire: &str, schema: &Schema) -> Result<Decoded, PactError> {
    let lines: Vec<&str> = wire.trim().split('\n').collect();
    if lines.len() < 2 {
        return Err(PactError("truncated message".into()));
    }
    let hdr = lines[0];
    let sid = hdr
        .rsplit("sid=")
        .next()
        .ok_or_else(|| PactError("malformed header".into()))?;
    if !hdr.starts_with('>') || sid.len() != 8 {
        return Err(PactError("malformed header".into()));
    }
    if sid != schema.sid() {
        return Err(PactError(format!("unknown schema sid={sid}; renegotiate")));
    }
    // Split an optional v0.3 field echo {f1,...} off the body line, then parse
    // the count marker. The echo is verified against the schema fail-closed.
    let body = lines[1];
    let (marker, echo): (&str, Option<&str>) = match body.find('{') {
        Some(i) if body.ends_with('}') => (&body[..i], Some(&body[i + 1..body.len() - 1])),
        _ => (body, None),
    };
    let star_body = format!("{}[*]", schema.name);
    let prefix = format!("{}[", schema.name);
    let declared: Option<usize> = if marker == star_body {
        None
    } else if marker.starts_with(&prefix) && marker.ends_with(']') {
        match marker[prefix.len()..marker.len() - 1].parse::<usize>() {
            Ok(n) => Some(n),
            Err(_) => return Err(PactError("schema name mismatch".into())),
        }
    } else {
        return Err(PactError("schema name mismatch".into()));
    };
    if let Some(e) = echo {
        let got: Vec<&str> = e.split(',').collect();
        let want: Vec<&str> = schema.fields.iter().map(|f| f.name.as_str()).collect();
        if got != want {
            return Err(PactError(format!(
                "field echo {got:?} != schema fields {want:?}"
            )));
        }
    }
    let star = declared.is_none();

    let mut records = Vec::new();
    let mut trailing: Option<usize> = None;
    for line in &lines[2..] {
        if line.starts_with('^') {
            continue; // provenance
        }
        if let Some(n) = is_trailer(line) {
            if !star {
                return Err(PactError("trailing #n= only valid with name[*]".into()));
            }
            if trailing.is_some() {
                return Err(PactError("duplicate #n= trailer".into()));
            }
            trailing = Some(n);
            continue;
        }
        let raw = split_unescaped(line);
        if raw.len() != schema.fields.len() {
            return Err(PactError(format!(
                "arity {} != {}",
                raw.len(),
                schema.fields.len()
            )));
        }
        let mut rec = Vec::new();
        for (cell, f) in raw.iter().zip(&schema.fields) {
            let v = coerce(cell, f)?;
            check_bounds(&v, f)?;
            rec.push(v);
        }
        records.push(rec);
    }
    let mut count_verified = true;
    if let Some(n) = declared {
        if records.len() != n {
            return Err(PactError(format!("declared {n} rows, got {}", records.len())));
        }
    } else if let Some(n) = trailing {
        if records.len() != n {
            return Err(PactError(format!("trailing #n={n} != {} rows", records.len())));
        }
    } else {
        count_verified = false;
    }
    Ok(Decoded { records, count_verified })
}

fn coerce(cell: &str, f: &Field) -> Result<Value, PactError> {
    if cell == "~" {
        return Ok(Value::Null);
    }
    match &f.ftype {
        FType::Int => cell
            .parse::<i64>()
            .map(Value::Int)
            .map_err(|_| PactError(format!("int field {}: bad literal {cell:?}", f.name))),
        FType::Float => cell
            .parse::<f64>()
            .map(Value::Float)
            .map_err(|_| PactError(format!("float field {}: bad literal {cell:?}", f.name))),
        FType::Bool => match cell {
            "T" => Ok(Value::Bool(true)),
            "F" => Ok(Value::Bool(false)),
            _ => Err(PactError(format!("bool field {}: bad literal {cell:?}", f.name))),
        },
        FType::Enum(vs) => {
            if vs.iter().any(|v| v == cell) {
                Ok(Value::Str(cell.to_string()))
            } else {
                Err(PactError(format!("{}={cell:?} outside enum", f.name)))
            }
        }
        FType::Str => Ok(Value::Str(unesc(cell))),
    }
}

fn check_bounds(v: &Value, f: &Field) -> Result<(), PactError> {
    let x = match v {
        Value::Int(i) => Some(*i as f64),
        Value::Float(x) => Some(*x),
        _ => None,
    };
    if let Some(x) = x {
        if let Some(lo) = f.lo {
            if x < lo {
                return Err(PactError(format!("{}={x} < lo={lo}", f.name)));
            }
        }
        if let Some(hi) = f.hi {
            if x > hi {
                return Err(PactError(format!("{}={x} > hi={hi}", f.name)));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn walls() -> Schema {
        Schema {
            name: "walls".into(),
            version: "0.2".into(),
            fields: vec![
                Field { name: "id".into(), ftype: FType::Str, lo: None, hi: None },
                Field {
                    name: "level".into(),
                    ftype: FType::Enum(vec!["L01".into(), "L02".into(), "L03".into(), "L04".into(), "ROOF".into()]),
                    lo: None, hi: None,
                },
                Field {
                    name: "thickness_mm".into(),
                    ftype: FType::Int,
                    lo: Some(50.0), hi: Some(600.0),
                },
                Field { name: "load_bearing".into(), ftype: FType::Bool, lo: None, hi: None },
            ],
        }
    }

    fn walls_full() -> Schema {
        Schema {
            name: "walls".into(),
            version: "0.2".into(),
            fields: vec![
                Field { name: "id".into(), ftype: FType::Str, lo: None, hi: None },
                Field { name: "level".into(),
                    ftype: FType::Enum(vec!["L01".into(),"L02".into(),"L03".into(),"L04".into(),"ROOF".into()]),
                    lo: None, hi: None },
                Field { name: "fire_rating".into(),
                    ftype: FType::Enum(vec!["EI30".into(),"EI60".into(),"EI90".into(),"EI120".into(),"NR".into()]),
                    lo: None, hi: None },
                Field { name: "thickness_mm".into(), ftype: FType::Int, lo: Some(50.0), hi: Some(600.0) },
                Field { name: "load_bearing".into(), ftype: FType::Bool, lo: None, hi: None },
                Field { name: "confidence".into(), ftype: FType::Float, lo: Some(0.0), hi: Some(1.0) },
            ],
        }
    }

    #[test]
    fn sid_parity_v02() {
        // Cross-language tripwire: Python & TS compute 226909cf for walls@0.2.
        assert_eq!(walls_full().sid(), "226909cf");
    }

    #[test]
    fn roundtrip() {
        let sc = walls();
        let recs = vec![
            vec![Value::Str("W-100".into()), Value::Str("L02".into()), Value::Int(200), Value::Bool(true)],
            vec![Value::Str("a,b\nc".into()), Value::Str("ROOF".into()), Value::Int(150), Value::Bool(false)],
        ];
        let wire = encode(&recs, &sc, "tell", "x", "p", "1");
        assert_eq!(decode(&wire, &sc).unwrap().records, recs);
    }

    #[test]
    fn fail_closed() {
        let sc = walls();
        let recs = vec![vec![Value::Str("W".into()), Value::Str("L01".into()), Value::Int(200), Value::Bool(true)]];
        let wire = encode(&recs, &sc, "tell", "x", "p", "1");
        assert!(decode(&wire.replace("L01", "L99"), &sc).is_err()); // enum
        assert!(decode(&wire.replace("200", "9999"), &sc).is_err()); // range
        assert!(decode(&wire.replace(",T", ""), &sc).is_err()); // arity
    }

    #[test]
    fn model_profile_dual() {
        let sc = walls();
        let recs = vec![
            vec![Value::Str("W-1".into()), Value::Str("L02".into()), Value::Int(200), Value::Bool(true)],
            vec![Value::Str("W-2".into()), Value::Str("ROOF".into()), Value::Int(150), Value::Bool(false)],
        ];
        let wire = encode_model(&recs, &sc, "tell", "extractor", "planner", "q", false);
        assert!(wire.contains("walls[*]"));
        assert!(wire.trim_end().ends_with("#n=2"));
        let d = decode(&wire, &sc).unwrap();
        assert_eq!(d.records, recs);
        assert!(d.count_verified);

        // row_truncation: drop the last value row, keep the #n=2 trailer -> reject
        let mut lines: Vec<&str> = wire.split('\n').collect();
        lines.remove(lines.len() - 2);
        assert!(decode(&lines.join("\n"), &sc).is_err());

        // name[*] with no trailer -> derived, count_verified=false (not an error)
        let no_trailer: Vec<&str> = { let mut l: Vec<&str> = wire.split('\n').collect(); l.pop(); l };
        let d2 = decode(&no_trailer.join("\n"), &sc).unwrap();
        assert_eq!(d2.records.len(), 2);
        assert!(!d2.count_verified);
    }

    #[test]
    fn field_echo_v03() {
        let sc = walls_full();
        // sid must NOT change under v0.3 (echo is message syntax only)
        assert_eq!(sc.sid(), "226909cf");
        let recs = vec![vec![
            Value::Str("W-1".into()), Value::Str("L02".into()),
            Value::Str("EI60".into()), Value::Int(200),
            Value::Bool(true), Value::Float(0.9),
        ]];
        let wire = encode_model(&recs, &sc, "tell", "extractor", "planner", "q", true);
        assert!(wire.contains("walls[*]{id,level,fire_rating,thickness_mm,load_bearing,confidence}"));
        assert_eq!(decode(&wire, &sc).unwrap().records, recs);
        // no-echo still decodes (optional)
        let plain = encode_model(&recs, &sc, "tell", "extractor", "planner", "q", false);
        assert_eq!(decode(&plain, &sc).unwrap().records, recs);
        // echo mismatch -> fail-closed
        assert!(decode(&wire.replace("fire_rating", "fire_rate"), &sc).is_err());
    }
}
