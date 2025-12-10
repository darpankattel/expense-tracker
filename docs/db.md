# 📦 DynamoDB Data Model — Expense Tracker

This document outlines the finalized DynamoDB schema and design choices for the Expense Tracker backend.
The goal is to support efficient querying, predictable performance, and scalable growth while keeping the model simple and cost-effective.

---

## 🔑 Table Structure

Each expense belongs to a single user, and all queries are scoped by the authenticated user.
A **single-table design** is used.

### **Primary Keys**

* **PK (Partition Key)**: `USER#<userID>`
* **SK (Sort Key)**: `DATE#<receiptDate>#<expenseID>`

This structure allows:

* efficient retrieval of all expenses of a user,
* natural filtering and sorting by `receiptDate`,
* uniqueness via appended `expenseID`.

---

## 🧾 Item Attributes

| Attribute        | Type         | Required | Description                      |
| ---------------- | ------------ | -------- | -------------------------------- |
| **userID**       | String       | Yes      | Extracted from Cognito identity. |
| **expenseID**    | String       | Yes      | UUID generated in Lambda.        |
| **merchantName** | String       | Yes      | Merchant or vendor name.         |
| **category**     | String       | Optional | Expense category.                |
| **amount**       | Number       | Yes      | Expense amount.                  |
| **receiptDate**  | String (ISO) | Yes      | Date shown on the receipt.       |
| **createdAt**    | String (ISO) | Yes      | Timestamp of creation.           |
| **updatedAt**    | String (ISO) | Optional | Updated timestamp.               |
| **receipt**      | Map          | Optional | S3 file metadata.                |
| **others**       | Map          | Optional | Flat key-value metadata.         |

---

## 🗂 Receipt Storage (S3 Reference Format)

If an uploaded receipt is present, metadata is stored in the `receipt` map:

```json
{
  "bucket": "string",
  "key": "string",
  "content_type": "string",
  "created_at": "string"
}
```

This enables frontend retrieval via pre-signed URLs, and keeps DynamoDB cost minimal by avoiding binary storage.

---

## 🧭 Global Secondary Indexes

### **GSI1 — Expense ID Lookup**

Used for direct lookup of an expense by its unique ID.

* **Index Name:** `GSIExpenseIDLookup`
* **GSI PK:** `EXPENSE#<expenseID>`
* **GSI SK:** `USER#<userID>`

**Purpose:**

* Enables the endpoint `GET /expenses/{id}` or internal validation checks.
* Avoids scanning a user partition when only the expenseID is known.

---

## 📚 Example Item

```json
{
  "PK": "USER#12345",
  "SK": "DATE#2025-01-12T09:32:11Z#cbd8ac1d-18c0-4c12-b1ab-93b7ad1b83c2",

  "userID": "12345",
  "expenseID": "cbd8ac1d-18c0-4c12-b1ab-93b7ad1b83c2",

  "merchantName": "Bhatbhateni Superstore",
  "category": "Groceries",
  "amount": 540.75,

  "receiptDate": "2025-01-12T00:00:00Z",
  "createdAt": "2025-01-12T09:32:11Z",
  "updatedAt": "2025-01-12T09:32:11Z",

  "receipt": {
    "bucket": "expense-receipts",
    "key": "12345/cbd8ac1d-18c0-4c12-b1ab-93b7ad1b83c2.jpg",
    "content_type": "image/jpeg",
    "created_at": "2025-01-12T09:32:11Z"
  },

  "others": {
    "notes": "some information here",
    "paymentMethod": "esewa",
    "location": "Kathmandu",
  },

  "GSI1PK": "EXPENSE#cbd8ac1d-18c0-4c12-b1ab-93b7ad1b83c2",
  "GSI1SK": "USER#12345"
}
```

---

## 🧩 Design Rationale

* **Date-prefixed SK** allows chronological queries and range filtering.
* **UUID-based expenseID** ensures uniqueness and supports GSI lookups.
* **Map fields** (`receipt`, `others`) minimize schema changes.
* **S3 object metadata** avoids storing binary payloads in DynamoDB.
* **Duplicated keys for GSI** maintain strict single-table patterns.
* **ISO timestamps** enforce ordering and simplify TTL or archival later.

