-- ============================================================================
-- Qontext — vocabulary seed data
-- ============================================================================
-- Run this AFTER schema.sql and AFTER any sqlite-vec / FTS5 setup.
-- Edit these values freely; the system reads behavior from this data.
-- ============================================================================

-- Entity types
INSERT OR REPLACE INTO entity_types (type, description) VALUES
    ('Person',        'A human actor — employee, customer, contact'),
    ('Organization',  'A company, department, or team'),
    ('Document',      'A standalone document — email, PDF, page'),
    ('Project',       'A bounded body of work'),
    ('Ticket',        'An issue, bug, or support case'),
    ('Policy',        'A rule, SOP, or formal guideline'),
    ('Product',       'A product or SKU'),
    ('Meeting',       'A scheduled event with attendees'),
    ('Message',       'A discrete communication — email, chat'),
    ('Event',         'A noteworthy occurrence in time');

-- Source authority weights — tunable
INSERT OR REPLACE INTO source_types (type, authority, description) VALUES
    ('hr',      1.00, 'HR system of record'),
    ('crm',     0.80, 'CRM database'),
    ('policy',  0.70, 'Policy document'),
    ('ticket',  0.50, 'Support or IT ticket'),
    ('email',   0.40, 'Email message'),
    ('chat',    0.30, 'Chat message'),
    ('unknown', 0.50, 'Unclassified source');

-- Predicate vocabulary
INSERT OR REPLACE INTO predicates (name, is_functional, description) VALUES
    ('works_at',    1, 'Person currently employed by Organization'),
    ('reports_to',  1, 'Person reports directly to another Person'),
    ('manages',     0, 'Person manages another Person, Project, or Team'),
    ('owns',        0, 'Person or Org owns a Project or Ticket'),
    ('part_of',     1, 'Sub-thing belongs to a parent thing'),
    ('mentions',    0, 'Document mentions an entity'),
    ('authored',    1, 'Person authored a Document'),
    ('attended',    0, 'Person attended a Meeting or Event'),
    ('references',  0, 'Document references another Document'),
    ('supersedes',  1, 'Newer Document replaces an older one'),
    ('located_in',  1, 'Entity is located in a place'),
    ('has_title',   1, 'Person currently holds a job title (literal object)'),
    ('has_email',   1, 'Person has an email address (literal object)'),
    ('has_status',  1, 'Project or Ticket current status (literal object)');
