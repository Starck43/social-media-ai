from wtforms.fields import Field
from wtforms.widgets import TextInput

from app.utils.date_parsing import universal_date_parser


class EuropeanDateField(Field):
	"""
	Поле для дат в формате DD-MM-YYYY с автоматическим преобразованием в DateTime
	"""
	widget = TextInput()

	def _value(self):
		if self.data:
			return self.data.strftime('%d-%m-%Y')
		return ''

	def process_formdata(self, valuelist):
		if not valuelist:
			return

		date_str = valuelist[0].strip()
		if not date_str:
			self.data = None
			return

		try:
			self.data = universal_date_parser(date_str, target_timezone='UTC+3')
			if not self.data:
				raise ValueError('Неверный формат даты. Используйте ДД-ММ-ГГГГ')
		except ValueError:
			self.data = None
			raise ValueError('Неверный формат даты. Используйте ДД-ММ-ГГГГ')
