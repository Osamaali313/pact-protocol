use pact_wire::*;

fn main() {
    let sc = Schema {
        name: "walls".into(),
        version: "0.2".into(),
        fields: vec![
            Field { name: "id".into(), ftype: FType::Str, lo: None, hi: None },
            Field { name: "level".into(),
                    ftype: FType::Enum(vec!["L01".into(), "L02".into()]),
                    lo: None, hi: None },
            Field { name: "thickness_mm".into(), ftype: FType::Int,
                    lo: Some(50.0), hi: Some(600.0) },
        ],
    };
    let recs = vec![vec![Value::Str("W-100".into()),
                         Value::Str("L02".into()), Value::Int(200)]];
    let wire = encode(&recs, &sc, "tell", "extractor", "planner", "7f3a");
    println!("{wire}");
    println!("decoded rows: {}", decode(&wire, &sc).unwrap().records.len());
}
