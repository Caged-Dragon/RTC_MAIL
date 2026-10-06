-- Company details reflected from the supplied RT Crackers portfolio screenshots.
update public.company_profile
set
  company_name = 'REDTHUNDER CRACKERS',
  legal_name = coalesce(nullif(legal_name,''), 'Rajukanna Fireworks'),
  tagline = 'Light up your celebrations',
  description = 'Makka & Wheat brand, the leading cracker and fireworks brand of Rajukanna Fireworks, Sivakasi, has served customers across India since 1995. RT Crackers is the online direct factory outlet for Makka & Wheat brand crackers.',
  vision = 'Make celebrations more memorable with superior quality RT Crackers products all over Tamil Nadu.',
  aim = 'Provide a wide range of quality crackers and fireworks with reliable service and reasonable factory-direct pricing.',
  mission = 'Lighten up millions of faces through professional service and quality cracker products in the fireworks industry.',
  physical_address = 'D No. 4/2017/A, Pothigai Nagar, Kila Thiruthangal',
  city = 'Sivakasi',
  state = 'Tamil Nadu',
  postal_code = '626189',
  country = 'India',
  email = 'sales@rtcrackers.com',
  support_email = 'support@rtcrackers.com',
  mobile = '7358737658',
  alternate_mobile = '8124100501',
  whatsapp_number = '8124100501',
  contact_details = coalesce(contact_details,'{}'::jsonb) || jsonb_build_object('sales_email','sales@rtcrackers.com','helpline','7358737658','whatsapp','8124100501'),
  extra_details = coalesce(extra_details,'{}'::jsonb) || jsonb_build_object('brand','Makka & Wheat','parent_business','Rajukanna Fireworks','established_claim','1995'),
  updated_at = now()
where is_active = true;
