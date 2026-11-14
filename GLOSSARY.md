# Airport Search

Search over commercial airports so a traveller can find the airport they mean by code, city, region, alias or a name in another language.

## Language

**Airport document**:
One searchable record for a commercial airport.
_Avoid_: Airport record, airport row, entry

**Served field**:
A field of the Airport document that users of the search see in results.
_Avoid_: Public field, output field, visible field

**Contract**:
The agreed list of Served fields, their types, which are optional, and the allowed values of each.
_Avoid_: Schema, spec, model
